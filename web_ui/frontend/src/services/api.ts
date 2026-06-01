const getBaseUrl = () => {
    const isDev = window.location.port === '5173'; // Vite default port
    const host = isDev ? 'localhost:8001' : window.location.host;
    return `http://${host}/api`;
};

const API_BASE = getBaseUrl();

export interface ProcessStatus {
    pid: number;
    script: string;
    status: 'running' | 'stopped' | 'failed';
    start_time: string | null;
    end_time: string | null;
    config_path: string | null;
}

export const api = {
    async getScripts(): Promise<string[]> {
        const res = await fetch(`${API_BASE}/scripts`);
        return res.json();
    },

    async getConfigs(): Promise<string[]> {
        const res = await fetch(`${API_BASE}/configs`);
        return res.json();
    },

    async getConfig(path: string): Promise<string> {
        const res = await fetch(`${API_BASE}/config/${encodeURIComponent(path)}`);
        if (!res.ok) throw new Error('Failed to load config');
        return res.text();
    },

    async updateConfig(path: string, content: string): Promise<void> {
        const res = await fetch(`${API_BASE}/config/${encodeURIComponent(path)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'text/plain' },
            body: content,
        });
        if (!res.ok) throw new Error('Failed to save config');
    },

    async getProcesses(): Promise<ProcessStatus[]> {
        const res = await fetch(`${API_BASE}/processes`);
        return res.json();
    },

    async startProcess(script: string, configPath: string): Promise<{pid: number, status: string}> {
        const res = await fetch(`${API_BASE}/process/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ script, config_path: configPath }),
        });
        if (!res.ok) throw new Error('Failed to start process');
        return res.json();
    },

    async stopProcess(pid: number): Promise<void> {
        const res = await fetch(`${API_BASE}/process/stop/${pid}`, {
            method: 'POST',
        });
        if (!res.ok) throw new Error('Failed to stop process');
    }
};

export const getWsBase = () => {
    const isDev = window.location.port === '5173';
    const host = isDev ? 'localhost:8001' : window.location.host;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${host}/ws/logs`;
};

export const WS_BASE = getWsBase();
