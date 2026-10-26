import os
import sys
import pytest

# Añadir el directorio core al path para pruebas
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../core"))
)

import main


def test_is_complex_url():
  # Verificamos que detecte correctamente los dominios complejos predeterminados
  assert main.is_complex_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is True
  assert main.is_complex_url("https://youtu.be/dQw4w9WgXcQ") is True
  assert main.is_complex_url("https://www.tiktok.com/@user/video/123") is True

  # Verificamos que descarte URLs comunes o ajenas
  assert main.is_complex_url("https://example.com/file.zip") is False
  assert main.is_complex_url("https://github.com/index.html") is False
