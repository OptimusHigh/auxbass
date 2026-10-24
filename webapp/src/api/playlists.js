import api, { cacheable, nonCacheable } from './core'

export const playlistsApi = {
  getAll: cacheable((params = {}, options = {}) => api.get('/playlists', { params, ...options })),
  getGlobal: cacheable((params = {}, options = {}) => api.get('/playlists/global', { params, ...options })),
  getManageAll: cacheable((options = {}) => api.get('/playlists/manage/all', options)),
  getOne: cacheable((id, params = {}, options = {}) => api.get(`/playlists/${id}`, { params, ...options })),
  // Bypass cache when shuffle is requested to get fresh random order
  // Add timestamp to ensure truly random results on every call
  getIds: (id, params = {}) => api.get(`/playlists/${id}/ids`, { 
    params: params.shuffle 
      ? { ...params, _t: Date.now() }  // Add timestamp to bypass any caching
      : params, 
    bypassCache: params.shuffle === true 
  }),
  create: nonCacheable((data) => api.post('/playlists', data), 'playlist'),
  update: nonCacheable((id, data) => api.put(`/playlists/${id}`, data), 'playlist'),
  delete: nonCacheable((id) => api.delete(`/playlists/${id}`), 'playlist'),
  subscribe: nonCacheable((id) => api.post(`/playlists/${id}/subscribe`), 'playlist'),
  unsubscribe: nonCacheable((id) => api.delete(`/playlists/${id}/subscribe`), 'playlist'),
  addTrack: nonCacheable((playlistId, trackId) => api.post(`/playlists/${playlistId}/tracks`, { track_id: trackId }), 'playlist'),
  removeTrack: nonCacheable((playlistId, trackId) => api.delete(`/playlists/${playlistId}/tracks/${trackId}`), 'playlist'),
  reorder: nonCacheable((id, trackIds) => api.put(`/playlists/${id}/reorder`, { track_ids: trackIds }), 'playlist'),
  uploadCover: nonCacheable((id, file) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post(`/playlists/${id}/cover`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
  }, 'playlist'),
  deleteCover: nonCacheable((id) => api.delete(`/playlists/${id}/cover`), 'playlist'),
  getUserPlaylists: cacheable((userId) => api.get(`/playlists/user/${userId}`)),
}
