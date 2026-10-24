import api, { cacheable } from './core'

export const artistsApi = {
  getAll: cacheable((params = {}, options = {}) => api.get('/artists', { params, ...options })),
  getGlobal: cacheable((params = {}, options = {}) => api.get('/artists/global', { params, ...options })),
  getOne: cacheable((artistName, params = {}, options = {}) => api.get(`/artists/${encodeURIComponent(artistName)}`, { params, ...options })),
  getInfo: cacheable((artistName, params = {}, options = {}) => api.get(`/artists/${encodeURIComponent(artistName)}/info`, { params, ...options })),
  getTracks: cacheable((artistName, params = {}, options = {}) => api.get(`/artists/${encodeURIComponent(artistName)}/tracks`, { params, ...options })),
  getImage: cacheable((artistName, options = {}) => api.get(`/artists/${encodeURIComponent(artistName)}/image`, options)),
}
