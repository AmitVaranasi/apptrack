create type app_stage as enum
  ('referral','applied','screening','assessment','interview','offer','rejected');

create table gmail_accounts (
  user_id          uuid primary key default gen_random_uuid(),
  google_sub       text unique not null,
  email            text not null,
  refresh_token    text not null,
  last_history_id  text,
  ghosted_after_days int default 21,
  created_at       timestamptz default now()
);

create table applications (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references gmail_accounts(user_id) on delete cascade,
  company       text not null,
  role          text,
  current_stage app_stage not null default 'applied',
  via_referral  boolean default false,
  first_seen    timestamptz not null,
  last_updated  timestamptz not null,
  notes         text,
  manual_override boolean default false,
  unique (user_id, company, role)
);

create table emails (
  id               uuid primary key default gen_random_uuid(),
  application_id   uuid not null references applications(id) on delete cascade,
  gmail_message_id text not null unique,
  subject          text,
  from_addr        text,
  received_at      timestamptz,
  detected_stage   app_stage,
  confidence       real,
  needs_review     boolean default false
);

create index applications_user_stage_idx on applications(user_id, current_stage);
create index emails_application_received_idx on emails(application_id, received_at);
