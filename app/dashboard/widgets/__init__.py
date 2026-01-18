"""
Dashboard Widget Data Providers

Each widget has its own module with:
- Data fetching function(s)
- Type definitions
- Caching logic if needed
"""

from . import billing_widget

__all__ = [
    'billing_widget',
]
