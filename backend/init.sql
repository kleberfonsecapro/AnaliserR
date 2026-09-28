CREATE TABLE IF NOT EXISTS analises_mvp (
    id SERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    relatorio_gerado TEXT NOT NULL,
    data_criacao TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS analises_mvp_telegram_user_id_idx
    ON analises_mvp (telegram_user_id);

CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    senha_hash TEXT NOT NULL,
    papel TEXT NOT NULL CHECK (papel IN ('admin', 'gestor', 'usuario')),
    telegram_user_id BIGINT UNIQUE,
    pode_usar_bot BOOLEAN NOT NULL DEFAULT FALSE,
    data_criacao TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
