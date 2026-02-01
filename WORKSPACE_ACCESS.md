# Nasiya365 Workspace Access Guide

## The workspace exists and is configured correctly!

Database verification shows:
- ✅ Workspace Name: Nasiya365
- ✅ Module: nasiya365  
- ✅ Icon: money (💰)
- ✅ Public: Yes (1)
- ✅ Hidden: No (0)
- ✅ Sequence: 10 (should appear early in list)

## How to Access:

### Method 1: Direct URL
Navigate directly to the workspace:
```
http://localhost:8089/app/nasiya365
```

### Method 2: Access DocTypes Directly
You can access any of the custom doctypes directly:

- Customer Profile: http://localhost:8089/app/customer-profile
- Product: http://localhost:8089/app/product
- Sales Order: http://localhost:8089/app/sales-order
- Contract: http://localhost:8089/app/contract
- Installment Plan: http://localhost:8089/app/installment-plan
- Payment Transaction: http://localhost:8089/app/payment-transaction
- Stock Entry: http://localhost:8089/app/stock-entry
- Branch: http://localhost:8089/app/branch
- Warehouse: http://localhost:8089/app/warehouse

### Method 3: Use Awesome Bar
1. Press `Ctrl+K` (or `Cmd+K` on Mac) to open the search
2. Type "Customer Profile" or "Product" or any doctype name
3. Press Enter to access

### Method 4: Check Module Page
Navigate to:
```
http://localhost:8089/app/modules
```
Then click on "Nasiya365" module

## Troubleshooting

If the workspace still doesn't appear in the sidebar:

1. **Clear browser cache completely**:
   - Chrome/Edge: Ctrl+Shift+Delete → Clear all time
   - Firefox: Ctrl+Shift+Delete → Everything

2. **Try incognito/private mode**

3. **Check user permissions**: Make sure Administrator has access to the Nasiya365 module

4. **Rebuild the desk**: 
   ```
   docker-compose -f docker-compose.prod.yml exec backend bench --site my.nasiya365.uz build
   ```

## Available Doctypes

All these are installed and working:
- Branch, Branch User
- Cashbox, Cashbox Transaction  
- Category Attribute
- Collector
- Contract
- Customer Profile, Customer Phone Number
- Installment Plan, Installment Schedule
- Merchant Settings
- Payment Transaction
- Print Template
- Product, Product Category, Product Attribute Template, Product Attribute Value
- Sales Order, Sales Order Item
- SMS Gateway Settings
- Stock Entry, Stock Entry Item, Stock Ledger
- Warehouse
