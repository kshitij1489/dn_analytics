"""
Weather Helper Utilities
Shared functions for weather-related data processing.
"""


def get_rain_cat(mm: float) -> str:
    """
    Categorize rain amount into human-readable categories.
    
    Args:
        mm: Precipitation in millimeters
        
    Returns:
        'heavy' if >= 2.5mm, 'drizzle' if >= 0.6mm, else 'none'
    """
    if mm >= 2.5:
        return "heavy"
    if mm >= 0.6:
        return "drizzle"
    return "none"
