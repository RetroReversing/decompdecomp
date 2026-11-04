CREATE TABLE projects (
  id TEXT PRIMARY KEY,
  origin TEXT NOT NULL,
  room TEXT NOT NULL,
  title TEXT NOT NULL,
  platform TEXT NOT NULL,
  summary TEXT NOT NULL,
  revision TEXT NOT NULL,
  owner_hash TEXT NOT NULL,
  published_at INTEGER NOT NULL,
  checked_at INTEGER NOT NULL,
  success_at INTEGER NOT NULL,
  next_check INTEGER NOT NULL,
  failures INTEGER NOT NULL DEFAULT 0,
  UNIQUE(origin, room)
);
CREATE INDEX due_projects ON projects(next_check);
CREATE INDEX project_titles ON projects(title);
CREATE TABLE registrations (
  id TEXT PRIMARY KEY,
  origin TEXT NOT NULL,
  room TEXT NOT NULL,
  challenge TEXT NOT NULL,
  expires INTEGER NOT NULL
);
CREATE TABLE rate_limits (
  key TEXT PRIMARY KEY,
  count INTEGER NOT NULL,
  expires INTEGER NOT NULL
);
