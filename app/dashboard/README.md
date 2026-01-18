# DEPRECATED - Dashboard Data Providers

**This module has been moved to: `app/frontend/`**

## New Location

The new structure organizes backend data by frontend page/component:

```
app/frontend/
├── __init__.py
├── README.md
└── dashboard/
    ├── __init__.py
    ├── account_balance_card.py  # AccountBalanceCard
    └── ...
```

## New Usage

```python
from app.frontend.dashboard import get_account_balance_data

data = await get_account_balance_data(user_id, company_id, mandate_path)
```

## Documentation

See: `app/frontend/README.md`
