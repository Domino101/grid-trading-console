const TOKEN_KEY = 'GRID_API_TOKEN';

export const getStoredToken = () => {
  if (typeof localStorage === 'undefined') return '';
  return localStorage.getItem(TOKEN_KEY) ?? '';
};

export const storeToken = (token: string) => {
  localStorage.setItem(TOKEN_KEY, token.trim());
};

export const clearStoredToken = () => {
  localStorage.removeItem(TOKEN_KEY);
};

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }

  get isUnauthorized() {
    return this.status === 401 || this.status === 403;
  }
}

export const detailMessage = (body: unknown, fallback: string) => {
  if (!body || typeof body !== 'object') return fallback;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === 'string' && message.trim()) return message;
  }
  return fallback;
};
