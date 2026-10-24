import api, { cacheable } from './core'

export const albumsApi = {
  getAll: cacheable((params = {}, options = {}) => api.get('/albums', { params, ...options })),
  getGlobal: cacheable((params = {}, options = {}) => api.get('/albums/global', { params, ...options })),
  getOne: cacheable((id, options = {}) => api.get(`/albums/${id}`, options)),
  resolve: (params = {}, options = {}) => api.get('/albums/resolve', { params, ...options }),
  // Bypass cache when shuffle is requested to get fresh random order
  // Add timestamp to ensure truly random results on every call
  getIds: (id, params = {}) => api.get(`/albums/${id}/ids`, { 
    params: params.shuffle 
      ? { ...params, _t: Date.now() }  // Add timestamp to bypass any caching
      : params, 
    bypassCache: params.shuffle === true 
  }),
}
