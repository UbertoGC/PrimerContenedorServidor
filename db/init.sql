CREATE DATABASE IF NOT EXISTS miapp;

USE miapp;

CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    usuario TEXT,
    contra TEXT
);