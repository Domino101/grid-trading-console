# Manual Trade Approval Flooding Fix Walkthrough

We diagnosed and resolved the issue where the bot was rapidly flooding the logs/terminal with manual trade approval requests, and implemented robust filtering and deduplication controls to prevent future log spam.

## 1. Root Cause Analysis
The manual trade approval mechanism in `main.py` relied on `asyncio.wait()` to wait for either a Telegram confirmation or a timeout event:
```python
done, pending = await asyncio.wait(
    [confirm_event.wait(), ignore_event.wait()],
    timeout=10.0,
    return_when=asyncio.FIRST_COMPLETED
)
```
In modern Python versions (Python 3.8+), **passing raw coroutine objects to `asyncio.wait()` is forbidden** and raises a `TypeError` (`Passing coroutines is forbidden, use tasks explicitly.`). 

When this exception was raised:
1. It bypassed the block where the **trade cooldown** was set.
2. The `except Exception` handler caught the error, logged it, and immediately called `continue` to restart the processing loop.
3. Because no cooldown was applied to the opportunity, the loop immediately picked up the same arbitrage opportunity on the next tick, re-prompting Telegram, hitting the same error, and repeating infinitely.
4. This tight infinite loop flooded `bot.log` until it reached over **140 MB** in size and consumed significant CPU resources.

Furthermore, we found that standard opportunity logging was printing full cards and writing DB entries even when the opportunity was in active trade cooldown or already pending manual confirmation, resulting in excessive terminal output.

---

## 2. Implemented Fixes

### A. Wrapping Coroutines in Tasks
In [main.py](file:///usr/local/lib/crypto-option-bot/main.py#L320-L337), we wrapped the `wait()` coroutines into explicit asyncio tasks before calling `asyncio.wait()`:
```python
confirm_task = asyncio.create_task(confirm_event.wait())
ignore_task = asyncio.create_task(ignore_event.wait())
try:
    done, pending = await asyncio.wait(
        [confirm_task, ignore_task],
        timeout=10.0,
        return_when=asyncio.FIRST_COMPLETED
    )
    ...
finally:
    confirm_task.cancel()
    ignore_task.cancel()
```
* **Explicit Tasks**: Ensures compatibility with modern Python `asyncio` standards.
* **Cancellation in `finally`**: Guarantees that any pending task is cancelled immediately upon exiting the block, preventing resource/task leaks.

### B. Premature Deduplication & Fine-Grained Opportunity Log Levels
We restructured the scanning loop to perform trade cooldown and manual-pending checks **before** writing to the SQLite/JSONL recorders, printing to the terminal, or sending Telegram notifications:
1. **Low-Spread / Non-Actionable Check (`WAIT`)**: If `check_res.action == "WAIT"`, we log a single compact dictionary representation at `DEBUG` level and immediately `continue`.
2. **Cooldown Check**: We check `trade_cooldowns` next. If active, we print a single trace at `DEBUG` level and `continue`.
3. **Manual Pending Check**: We check `manual_pending_approvals` next. If already pending, we print a single trace at `DEBUG` level and `continue`.

This ensures that only unique, actionable, and ready-to-execute arbitrage opportunities will ever trigger full card logging, telemetry recording, or Telegram prompts.

### C. Fallback Cooldown in Exception Handler
As an extra layer of defense, we now set a fallback 15-second cooldown inside the `except Exception as e` block:
```python
except Exception as e:
    logger.error(f"Error waiting for Telegram manual confirmation: {e}")
    # Apply cooldown even on error to prevent CPU-burn loop if anything else goes wrong
    self.trade_cooldowns[cooldown_key] = time.time() + 15.0
    continue
```

---

## 3. Verification & Testing

### A. Dedicated Unit Tests
We added two dedicated tests to [test_arbitrage_engine.py](file:///usr/local/lib/crypto-option-bot/tests/test_arbitrage_engine.py):
1. `test_tc10_manual_mode_wait_coroutines_fix` to verify raw coroutines are converted to asyncio tasks without error.
2. `test_tc11_terminal_flood_prevention` to verify that when `action == "WAIT"`, opportunity evaluation only logs at `DEBUG` level and never spams the terminal or writes full card logs at `INFO` level.

### B. Execution Results
We ran the test suite and confirmed that all **45 tests passed successfully**!
```bash
PYTHONPATH=. venv/bin/pytest -p no:pytest_ethereum
================ 45 passed, 8 warnings in 4.63s =================
```

---

## 4. Housekeeping & Disk Cleanup
We safely terminated the running bot process which was trapped in the loop (`kill 2269171`) and truncated the bloated `bot.log` from **140 MB down to 8 KB** (keeping the latest 100 log lines for trace context), freeing up valuable disk space on the system.

---

## 5. Next Steps
You can now safely restart the bot with your preferred execution command:
```bash
venv/bin/python main.py --continuous
```
