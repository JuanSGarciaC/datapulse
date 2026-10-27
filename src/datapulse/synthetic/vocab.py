"""Vocabularios fijos para el generador.

Se usan listas propias (no Faker) a propósito: la salida de Faker cambia entre
versiones, lo que rompería la reproducibilidad byte a byte de los datos de
ejemplo. Incluye acentos y eñes para ejercitar UTF-8 de punta a punta.
"""

from typing import Final

FIRST_NAMES: Final = (
    "Ana",
    "Andrés",
    "Camila",
    "Carlos",
    "Daniela",
    "David",
    "Elena",
    "Felipe",
    "Gabriela",
    "Hugo",
    "Isabel",
    "Javier",
    "José",
    "Juliana",
    "Laura",
    "Luis",
    "María",
    "Mateo",
    "Natalia",
    "Nicolás",
    "Paula",
    "Pedro",
    "Sofía",
    "Tomás",
    "Valentina",
    "Sebastián",
    "Lucía",
    "Martín",
    "Ximena",
    "Íñigo",
)

LAST_NAMES: Final = (
    "García",
    "Rodríguez",
    "Martínez",
    "López",
    "González",
    "Pérez",
    "Sánchez",
    "Ramírez",
    "Torres",
    "Flores",
    "Rivera",
    "Gómez",
    "Díaz",
    "Cruz",
    "Morales",
    "Ortiz",
    "Gutiérrez",
    "Chávez",
    "Ramos",
    "Vargas",
    "Castillo",
    "Jiménez",
    "Moreno",
    "Romero",
    "Herrera",
    "Medina",
    "Aguilar",
    "Muñoz",
    "Rojas",
    "Peña",
)

# (ciudad, país ISO-3166 alfa-2)
CITIES: Final = (
    ("Bogotá", "CO"),
    ("Medellín", "CO"),
    ("Cali", "CO"),
    ("Barranquilla", "CO"),
    ("Ciudad de México", "MX"),
    ("Guadalajara", "MX"),
    ("Lima", "PE"),
    ("Santiago", "CL"),
    ("Buenos Aires", "AR"),
    ("Madrid", "ES"),
    ("Quito", "EC"),
)

SEGMENTS: Final = ("retail", "smb", "enterprise")

# Subconjunto de las categorías reales de DummyJSON (verificadas el 2026-10-27).
CATEGORIES: Final = (
    "beauty",
    "fragrances",
    "furniture",
    "groceries",
    "home-decoration",
    "kitchen-accessories",
    "laptops",
    "mens-shirts",
    "smartphones",
    "sports-accessories",
    "tablets",
    "womens-watches",
)

PRODUCT_ADJECTIVES: Final = (
    "Classic",
    "Premium",
    "Compact",
    "Eco",
    "Smart",
    "Ultra",
    "Vintage",
    "Pro",
)

ORDER_STATUSES: Final = ("placed", "paid", "shipped", "delivered", "cancelled", "returned")
