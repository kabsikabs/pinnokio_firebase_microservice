# Frontend Data Providers

This module organizes backend data fetching by frontend page/component.
Each subfolder corresponds to a page, and each file corresponds to a frontend component.

## Structure

```
frontend/
├── __init__.py
├── README.md                         # This file
└── dashboard/                        # Dashboard page
    ├── __init__.py
    ├── account_balance_card.py      # AccountBalanceCard component
    ├── storage_card.py              # StorageCard component (TODO)
    ├── metrics_cards.py             # MetricsCards component (TODO)
    ├── jobs_widget.py               # JobsWidget component (TODO)
    ├── approvals_widget.py          # ApprovalsWidget component (TODO)
    ├── tasks_widget.py              # TasksWidget component (TODO)
    ├── expenses_table.py            # ExpensesTable component (TODO)
    └── activity_feed.py             # ActivityFeed component (TODO)
```

## Component to Module Mapping

| Python Module              | Frontend Component    | Path                                              |
|---------------------------|----------------------|---------------------------------------------------|
| `account_balance_card.py` | AccountBalanceCard   | `src/components/dashboard/account-balance-card.tsx` |
| `storage_card.py`         | StorageCard          | `src/components/dashboard/storage-card.tsx`       |
| `metrics_cards.py`        | MetricsCards         | `src/components/dashboard/metrics-cards.tsx`      |
| `jobs_widget.py`          | JobsWidget           | `src/components/dashboard/jobs-widget.tsx`        |
| `approvals_widget.py`     | ApprovalsWidget      | `src/components/dashboard/approvals-widget.tsx`   |
| `tasks_widget.py`         | TasksWidget          | `src/components/dashboard/tasks-widget.tsx`       |
| `expenses_table.py`       | ExpensesTable        | `src/components/dashboard/expenses-table.tsx`     |
| `activity_feed.py`        | ActivityFeed         | `src/components/dashboard/activity-feed.tsx`      |

## Usage

```python
from app.frontend.dashboard import get_account_balance_data

# In dashboard_handlers.py
data = await get_account_balance_data(user_id, company_id, mandate_path)
```

## Account Balance Card

### Data Structure

```json
{
    "currentBalance": 256.02,       // current_topping - current_expenses
    "currentMonthExpenses": 150.00, // Sum of expenses for current month
    "lastMonthExpenses": 200.00,    // Sum of expenses for previous month
    "totalCost": 2743.98,           // Total of all expenses
    "totalTopping": 3000.00         // Total top-ups
}
```

### Data Sources

1. **Balance & Topping**: `FirebaseManagement.get_balance_info(mandate_path)`
   - Path: `clients/{user_id}/billing/current_balance`
   - Returns: `{current_balance, current_expenses, current_topping}`

2. **Monthly Costs**: Calculated from task_manager documents
   - Path: `{mandate_path}/task_manager/{task_id}`
   - Field: `billing.total_sales_price`
   - Filtered by: `timestamp` (year/month)

### Original Reflex Implementation

From `JobHistory.py`:
```python
@rx.var
def get_current_month_cost(self) -> float:
    """Sum of costs for current calendar month."""
    now = datetime.now()
    total = 0.0
    for exp in self.expenses:
        if exp.timestamp.year == now.year and exp.timestamp.month == now.month:
            total += float(exp.cost or 0.0)
    return total

@rx.var
def get_last_month_cost(self) -> float:
    """Sum of costs for previous calendar month."""
    now = datetime.now()
    prev_month = now.month - 1 if now.month > 1 else 12
    prev_year = now.year if now.month > 1 else now.year - 1
    total = 0.0
    for exp in self.expenses:
        if exp.timestamp.year == prev_year and exp.timestamp.month == prev_month:
            total += float(exp.cost or 0.0)
    return total
```

## Adding a New Component

1. Create `frontend/{page}/{component_name}.py`
2. Implement `async def get_{component_name}_data(user_id, company_id, ...) -> Dict[str, Any]`
3. Add docstring with frontend component mapping
4. Export in `frontend/{page}/__init__.py`
5. Update this README
6. Use in appropriate handler (e.g., `dashboard_handlers.py`)

## Architecture Flow

```
Frontend Component (Next.js)
        │
        ▼
WebSocket (orchestration)
        │
        ▼
dashboard_handlers.py (full_data)
        │
        ▼
frontend/dashboard/{component}.py
        │
        ▼
Firebase / External Services
```

## Notes

- All modules use async functions for non-blocking I/O
- mandate_path is passed from orchestration context
- Default values are returned on error to prevent frontend crashes
- Logging includes component name for easy debugging
