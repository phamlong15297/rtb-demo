CREATE TABLE IF NOT EXISTS pastes (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    shortlink CHAR(7) UNIQUE NOT NULL,
    s3_path VARCHAR(255) NOT NULL,
    created_at DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    burn_after_read BOOLEAN NOT NULL DEFAULT FALSE,
    password_hash VARCHAR(255) NULL,
    size INT NOT NULL
);
