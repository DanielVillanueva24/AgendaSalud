"""Extensiones compartidas (evita imports circulares entre app y models)."""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
