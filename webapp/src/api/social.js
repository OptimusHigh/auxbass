import api from './core'

export const socialApi = {
  searchFriends: (search, perPage = 50, page = 1, options = {}) => api.get('/social/friends/search', { params: { search, per_page: perPage, page }, ...options }),
  getFollowing: () => api.get('/social/following'),
  getFollowers: () => api.get('/social/followers'),
  searchUsers: (query, page = 1, perPage = 30) => api.get('/social/search', { params: { q: query, query, page, per_page: perPage } }),
  follow: (userId) => api.post('/social/follow', { user_id: userId }),
  unfollow: (userId) => api.post('/social/unfollow', { user_id: userId }),
  getUserLibrary: (userId, params = {}) => api.get(`/social/user/${userId}/library`, { params }),
  getUserTrackIds: (userId, params = {}) => api.get(`/social/user/${userId}/ids`, { 
    params: params.sort_by === 'random' ? { ...params, _t: Date.now() } : params,
    bypassCache: params.sort_by === 'random'
  }),
  getUserAlbums: (userId, params = {}) => api.get(`/social/user/${userId}/albums`, { params }),
  getUser: (userId, params = {}, options = {}) => api.get(`/social/user/${userId}`, { params, ...options }),
  getFeed: (scope = 'following', page = 1, perPage = 30) => api.get('/social/feed', { params: { scope, page, per_page: perPage } }),
  getUserExternalAccounts: (userId) => api.get(`/social/user/${userId}/external`, { bypassCache: true }),
  getUserExternalPlaylists: (userId, provider, params = {}) => api.get(`/social/user/${userId}/external/${provider}/playlists`, { params }),
  getUserExternalTracks: (userId, provider, params = {}) => api.get(`/social/user/${userId}/external/${provider}/tracks`, { params }),
  getUserExternalPlaylistTracks: (userId, providerOrPlaylistId, playlistIdOrParams = {}, params = {}) => {
    let playlistId = providerOrPlaylistId
    let actualParams = playlistIdOrParams
    if (typeof playlistIdOrParams === 'string' || typeof playlistIdOrParams === 'number') {
      playlistId = playlistIdOrParams
      actualParams = params
    }
    return api.get(`/social/user/${userId}/external/soundcloud/playlists/${playlistId}/tracks`, { params: actualParams })
  },
}
