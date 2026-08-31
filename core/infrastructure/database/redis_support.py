"""Optional Redis integration (Phase 2, P2-3/4/5).

Redis is treated as a *disposable* cache / coordination layer, never as the
ledger source of truth.  Every read/write has a database fallback.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from core.logging import get_logger

logger = get_logger(__name__)

GRID_EVENTS_CHANNEL = "grid:events"


def env_int(name: str, default: int) -> int:
    """Parse an integer env var, falling back to default on absence/garbage.

    F4 fix: GRID_REDIS_STATE_TTL / GRID_REDIS_LEADER_TTL were documented but
    never read; they now feed the cache TTL and leader lease respectively.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        logger.warning(f"⚠️ [Redis] 環境變數 {name}={raw!r} 不是有效整數，改用預設值 {default}")
        return default


def redis_enabled() -> bool:
    return bool(os.getenv("REDIS_URL"))


def _redis_client() -> Any:
    """Return a Redis client or None if REDIS_URL is absent / unavailable."""
    url = os.getenv("REDIS_URL")
    if not url:
        return None
    try:
        import redis
    except ImportError:  # pragma: no cover
        return None
    try:
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2.0)
        client.ping()
        return client
    except Exception as exc:
        logger.warning(f"⚠️ [Redis] 無法連線 REDIS_URL，自動降級回資料庫: {exc}")
        return None


_CLIENT: Optional[Any] = None
_CLIENT_LOCK = threading.Lock()


def get_redis() -> Optional[Any]:
    global _CLIENT
    if not redis_enabled():
        return None
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = _redis_client()
    return _CLIENT


def reset_redis_client() -> None:
    global _CLIENT
    with _CLIENT_LOCK:
        _CLIENT = None


def safe_redis(fn: Callable[[Any], Any], default: Any):
    """Run a Redis callback; swallow errors and return default (DB degrade)."""
    client = get_redis()
    if client is None:
        return default
    try:
        return fn(client)
    except Exception as exc:
        logger.warning(f"⚠️ [Redis] 操作失敗，降級回資料庫: {exc}")
        return default


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")


class RedisStateCache:
    """Hot cache for runtime snapshots.

    Keys:
      grid:state:{bot_id}          -> {"config_path", "stats", "updated_at"}
      grid:state:path:{b64}         -> same JSON (fast path lookup by config)

    TTL defaults to ``GRID_REDIS_STATE_TTL`` (env, default 300s) when not
    passed explicitly (F4 fix).
    """

    def __init__(self, ttl: Optional[int] = None):
        self.ttl = int(ttl) if ttl is not None else env_int("GRID_REDIS_STATE_TTL", 300)

    def _key_for_bot(self, bot_id: str) -> str:
        return f"grid:state:{bot_id}"

    def _key_for_path(self, config_path: str) -> str:
        return f"grid:state:path:{_b64(config_path)}"

    def set(self, bot_id: str, config_path: str, stats: dict, updated_at: float) -> None:
        value = json.dumps(
            {"config_path": config_path, "stats": stats, "updated_at": updated_at},
            ensure_ascii=False,
            default=str,
        )

        def _do(client: Any):
            client.setex(self._key_for_bot(bot_id), self.ttl, value)
            client.setex(self._key_for_path(config_path), self.ttl, value)

        safe_redis(_do, None)

    def get(self, config_path: str, bot_id: Optional[str] = None) -> Optional[Tuple[dict, float]]:
        def _do(client: Any):
            raw = client.get(self._key_for_path(config_path))
            if not raw and bot_id:
                raw = client.get(self._key_for_bot(bot_id))
            if not raw:
                return None
            try:
                data = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
            stats = data.get("stats")
            if not isinstance(stats, dict):
                return None
            return (stats, float(data.get("updated_at", 0.0)))

        return safe_redis(_do, None)

    def get_all(self) -> Dict[str, Tuple[dict, float]]:
        def _do(client: Any):
            result: Dict[str, Tuple[dict, float]] = {}
            for key in client.scan_iter("grid:state:path:*", count=200):
                if not isinstance(key, str) or not key.startswith("grid:state:path:"):
                    continue
                raw = client.get(key)
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                stats = data.get("stats")
                config_path = data.get("config_path")
                if isinstance(stats, dict) and isinstance(config_path, str) and config_path:
                    result[config_path] = (stats, float(data.get("updated_at", 0.0)))
            return result

        return safe_redis(_do, {})

    def delete(self, config_path: str, bot_id: Optional[str] = None) -> None:
        def _do(client: Any):
            keys = [self._key_for_path(config_path)]
            if bot_id:
                keys.append(self._key_for_bot(bot_id))
            client.delete(*keys)

        safe_redis(_do, None)


class RedisEventBus:
    """Pub/Sub for lifecycle / heartbeat / fill events (P2-4).

    F5 fix: the listener thread used to die with its socket when Redis
    restarted, silently stopping all downstream WebSocket pushes until the API
    process was restarted.  The subscription now reconnects with bounded
    backoff, and ``subscribe()`` returns a handle with a ``close()`` hook so
    API shutdown no longer leaks an unhandled traceback in the listener thread.
    """

    def __init__(self, channel: str = GRID_EVENTS_CHANNEL):
        self.channel = channel

    def publish(self, event: str, payload: dict) -> None:
        message = json.dumps({"event": event, **payload}, ensure_ascii=False, default=str)

        def _do(client: Any):
            client.publish(self.channel, message)

        safe_redis(_do, None)

    def subscribe(self, callback: Callable[[dict], None]) -> Optional[Dict[str, Any]]:
        """Return a subscription handle; caller must keep it referenced/close."""
        client = get_redis()
        if client is None:
            return None

        holder: Dict[str, Any] = {"running": True, "pubsub": None}

        def _connect(client_: Any) -> Any:
            pubsub = client_.pubsub()
            pubsub.subscribe(self.channel)
            holder["pubsub"] = pubsub
            return pubsub

        def _dispatch(raw: Any) -> None:
            if not isinstance(raw, dict) or raw.get("type") != "message":
                return
            try:
                data = json.loads(raw.get("data", "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                return
            try:
                callback(data)
            except Exception as exc:
                logger.warning(f"⚠️ [Redis] 事件回呼失敗: {exc}")

        def _thread() -> None:
            backoff = 1.0
            while holder["running"]:
                pubsub = holder.get("pubsub")
                try:
                    if pubsub is None:
                        fresh = get_redis()
                        if fresh is None:
                            # Redis still down/disabled; keep waiting, bounded.
                            time.sleep(backoff)
                            backoff = min(backoff * 2, 30.0)
                            continue
                        pubsub = _connect(fresh)
                        backoff = 1.0
                        logger.info("✅ [Redis] 事件訂閱已（重）連")
                    for raw in pubsub.listen():
                        if not holder["running"]:
                            break
                        _dispatch(raw)
                    if not holder["running"]:
                        break
                    # listen() ended without exception (e.g. closed remotely)
                    raise ConnectionError("redis pubsub listen ended")
                except Exception as exc:
                    if not holder["running"]:
                        break
                    logger.warning(f"⚠️ [Redis] 事件訂閱中斷，{backoff:.0f}s 後重連: {exc}")
                    try:
                        stale = holder.get("pubsub")
                        if stale is not None:
                            stale.close()
                    except Exception:
                        pass
                    holder["pubsub"] = None
                    reset_redis_client()
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)

        thread = threading.Thread(target=_thread, name="grid-redis-events", daemon=True)
        thread.start()

        def _close() -> None:
            holder["running"] = False
            pubsub = holder.get("pubsub")
            if pubsub is not None:
                try:
                    pubsub.close()
                except Exception:
                    pass

        return {"pubsub": holder.get("pubsub"), "thread": thread, "close": _close}


class RedisLeaderElection:
    """SETNX-based leadership for bot engine instances (P2-5).

    F7 hardening:
    * acquire() logs an explicit ERROR when Redis is configured but unreachable
      (fail-open preserved for single-host autonomy per ADR-2, no longer silent).
    * renew()/release() are atomic Lua compare-and-act scripts, so a stale
      owner can no longer renew/delete a key now owned by another engine.
    * the renewal loop tracks consecutive failures and ownership loss, logging
      ERROR and attempting one re-acquire instead of failing silently.

    TTL defaults to ``GRID_REDIS_LEADER_TTL`` (env, default 30s) when not
    passed explicitly (F4 fix).
    """

    _RENEW_SCRIPT = """
    local v = redis.call('GET', KEYS[1])
    if v == ARGV[1] then
      redis.call('EXPIRE', KEYS[1], ARGV[2])
      return 1
    end
    return 0
    """

    _RELEASE_SCRIPT = """
    local v = redis.call('GET', KEYS[1])
    if v == ARGV[1] then
      redis.call('DEL', KEYS[1])
      return 1
    end
    return 0
    """

    def __init__(self, bot_id: str, engine_id: Optional[str] = None, ttl: Optional[int] = None):
        self.bot_id = bot_id
        self.engine_id = engine_id or f"{os.getpid()}-{time.time_ns()}"
        self.ttl = int(ttl) if ttl is not None else env_int("GRID_REDIS_LEADER_TTL", 30)
        self._key = f"grid:leader:{bot_id}"
        self._renew_thread: Optional[threading.Thread] = None
        self._running = False
        self._renew_failures = 0
        self._lost_logged = False

    @property
    def key(self) -> str:
        return self._key

    def acquire(self) -> bool:
        """Try to become leader. Returns True when this engine owns the lock.

        Redis-unreachable keeps the documented fail-open semantics (the
        process-level/pgrep duplicate-engine guard remains authoritative),
        but the degradation is logged at ERROR level, not silently.
        """
        client = get_redis()
        if client is None:
            if redis_enabled():
                logger.error(
                    f"❌ [Leader] REDIS_URL 已設定但 Redis 不可達，領導者選舉降級放行"
                    f"（bot_id={self.bot_id} engine={self.engine_id}）；防雙開僅剩進程鎖防護"
                )
            return True

        def _do(c: Any) -> bool:
            owned = c.set(self._key, self.engine_id, nx=True, ex=self.ttl)
            if owned:
                logger.info(f"✅ [Leader] 取得領導權 bot_id={self.bot_id} engine={self.engine_id}")
                return True
            current = c.get(self._key)
            if isinstance(current, bytes):
                current = current.decode("utf-8", "replace")
            return current == self.engine_id

        try:
            owned = bool(_do(client))
        except Exception as exc:
            logger.error(f"❌ [Leader] Redis 操作失敗，領導者選舉降級放行 bot_id={self.bot_id}: {exc}")
            return True
        if owned:
            self._renew_failures = 0
            self._lost_logged = False
            self._start_renew()
        return owned

    def renew(self) -> bool:
        """Atomically renew the lease iff still the owner. False = lost/unknown."""

        def _do(client: Any) -> bool:
            return bool(client.eval(self._RENEW_SCRIPT, 1, self._key, self.engine_id, str(self.ttl)))

        result = safe_redis(_do, None)
        if result is None:
            # Redis unavailable: count consecutive failures for observability.
            self._renew_failures += 1
            if self._renew_failures == 3:
                logger.error(
                    f"❌ [Leader] 續約連續 {self._renew_failures} 次失敗（Redis 不可達），"
                    f"bot_id={self.bot_id}：租約到期後可能出現雙領導，請檢查 Redis"
                )
            return False
        self._renew_failures = 0
        return bool(result)

    def _start_renew(self) -> None:
        if self._renew_thread is not None:
            return
        self._running = True
        self._renew_thread = threading.Thread(
            target=self._renew_loop, name=f"leader-renew-{self.bot_id}", daemon=True
        )
        self._renew_thread.start()

    def _renew_loop(self) -> None:
        while self._running:
            time.sleep(max(1, self.ttl // 3))
            if self.renew():
                continue
            # Lease expired / unknown: try to retake ownership once per cycle
            # and log loudly while leadership is not (verifiably) ours.

            def _take(client: Any) -> bool:
                return bool(client.set(self._key, self.engine_id, nx=True, ex=self.ttl))

            if safe_redis(_take, False):
                self._lost_logged = False
                logger.warning(f"⚠️ [Leader] 租約曾中斷，已重新取得領導權 bot_id={self.bot_id}")
            elif not self._lost_logged:
                self._lost_logged = True
                logger.error(
                    f"❌ [Leader] 領導權已喪失且無法重新取得 bot_id={self.bot_id}"
                    f" engine={self.engine_id}（可能由另一引擎持有）"
                )

    def release(self) -> None:
        self._running = False

        def _do(client: Any) -> bool:
            return bool(client.eval(self._RELEASE_SCRIPT, 1, self._key, self.engine_id))

        safe_redis(_do, None)
        logger.info(f"🔓 [Leader] 釋放領導權 bot_id={self.bot_id} engine={self.engine_id}")


def leader_election_enabled() -> bool:
    """Enable leader election automatically when Redis is configured."""
    return redis_enabled()
