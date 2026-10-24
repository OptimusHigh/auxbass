-- Migration 012: Minecraft rank-based track limits
-- mc_rank: current rank id on GanjaCraft server (synced by ganj4core on JOIN/rankup)
-- mc_max_tracks: max tracks allowed in system playlist (🎮 Minecraft Media)

ALTER TABLE users ADD COLUMN mc_rank TEXT NOT NULL DEFAULT 'noob';
ALTER TABLE users ADD COLUMN mc_max_tracks INTEGER NOT NULL DEFAULT 5;
