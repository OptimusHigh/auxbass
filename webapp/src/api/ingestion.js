import api, { nonCacheable } from './core'

export const ingestionApi = {
  preview: (url) => api.post('/ingestion/preview', { url }),
  start: nonCacheable((url, selectedUrls = null, tracks = null, title = null, createPlaylist = false, playlistName = null, coverUrl = null) => api.post('/ingestion/start', {
    url,
    selected_urls: selectedUrls,
    tracks,
    title,
    create_playlist: createPlaylist,
    playlist_name: playlistName,
    cover_url: coverUrl,
  }), 'track'),
  getJob: (jobId) => api.get(`/ingestion/jobs/${jobId}`, { bypassCache: true }),
  cancelJob: (jobId) => api.post(`/ingestion/jobs/${jobId}/cancel`),
  getRecent: () => api.get('/ingestion/recent', { bypassCache: true }),
  search: (q, provider = 'soundcloud', limit = 30, options = {}) => api.get('/ingestion/search', { params: { q, provider, limit }, ...options }),
  quickImport: nonCacheable((data) => api.post('/ingestion/quick-import', data), 'track'),
  getSoundCloudAccount: () => api.get('/ingestion/account/soundcloud', { bypassCache: true }),
  connectSoundCloudAccount: nonCacheable((data) => api.post('/ingestion/account/soundcloud/connect', data), 'externalAccount'),
  disconnectSoundCloudAccount: nonCacheable(() => api.delete('/ingestion/account/soundcloud'), 'externalAccount'),
  getSoundCloudLikes: (params = {}) => api.get('/ingestion/account/soundcloud/likes', { params }),
  getSoundCloudTracks: (params = {}) => api.get('/ingestion/account/soundcloud/tracks', { params }),
  getSoundCloudPlaylists: (params = {}) => api.get('/ingestion/account/soundcloud/playlists', { params }),
  getSoundCloudPlaylistTracks: (playlistId, params = {}) => api.get(`/ingestion/account/soundcloud/playlists/${playlistId}/tracks`, { params }),
  getSpotifyAccount: () => api.get('/ingestion/account/spotify', { bypassCache: true }),
  connectSpotifyAccount: nonCacheable((data) => api.post('/ingestion/account/spotify/connect', data), 'externalAccount'),
  disconnectSpotifyAccount: nonCacheable(() => api.delete('/ingestion/account/spotify'), 'externalAccount'),
  updateAccountPrivacy: nonCacheable((provider, data) => api.patch(`/ingestion/account/${provider}/privacy`, data), 'externalAccount'),
  getSpotifyLikes: (params = {}) => api.get('/ingestion/account/spotify/likes', { params }),
  previewExportifyCsv: (formData) => api.post('/ingestion/spotify/exportify/preview', formData, {
    headers: { 'Content-Type': 'multipart/form-data' }
  }),
  getLastSpotifyImport: () => api.get('/ingestion/spotify/last-import', { bypassCache: true }),
  previewLastSpotifyImport: (params = {}) => api.get('/ingestion/spotify/last-import/preview', { params, bypassCache: true }),
  deleteSpotifyImportFile: nonCacheable((fileId) => api.delete(`/ingestion/spotify/import-file/${fileId}`), 'externalAccount'),
  startExportifyImport: nonCacheable((data) => api.post('/ingestion/spotify/exportify/start', data), 'track'),
}
