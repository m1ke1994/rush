const configuredApiBaseUrl = String(import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8011').trim()

export const API_BASE_URL = configuredApiBaseUrl.replace(/\/+$/, '')

export function apiUrl(path) {
  return `${API_BASE_URL}/${String(path).replace(/^\/+/, '')}`
}
