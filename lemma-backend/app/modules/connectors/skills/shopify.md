# Shopify

Shopify is the commerce platform behind a store's products, orders, customers, inventory and fulfilment. Use it to look up orders, search customers, manage the catalog and check stock.

**Auth config name:** `shopify`

## Setup

Shopify connects through the organization's own Shopify app. Lemma holds no Shopify credentials, and the API-token route is not offered because its tokens cannot be refreshed.

1. In the Shopify Dev Dashboard, create an app and add `https://backend.composio.dev/api/v1/auth-apps/add` to its allowed redirect URLs.
2. Install it for the organization with the app's client id and secret. `scopes` is optional and comma separated; leave it out to request Composio's defaults.
   ```
   lemma connectors auth-configs create shopify --kind composio --name shopify \
     --config-source ORG_CUSTOM -d '{"client_id": "...", "client_secret": "...", "scopes": "read_orders,read_products,write_products"}'
   ```
3. Connect an account. Every connection needs the **store name**: the `acme` in `acme.myshopify.com`, not the full domain and not a custom domain.
   ```
   lemma connectors connect-requests create shopify --field subdomain=acme
   ```
   The dialog in the app asks for it before sending you to Shopify.

## Common Tasks

### Check which store an account is connected to
```
lemma connectors operations execute shopify SHOPIFY_GET_SHOP_DETAILS -d '{}'
```

### List recent orders
```
lemma connectors operations execute shopify SHOPIFY_GET_ORDER_LIST -d '{"status": "any", "limit": 50}'
```

### Read one order
```
lemma connectors operations execute shopify SHOPIFY_GET_ORDERSBY_ID -d '{"order_id": "5551234567890"}'
```

### Find a customer
```
lemma connectors operations execute shopify SHOPIFY_GET_CUSTOMERS_SEARCH -d '{"query": "email:anukul@lemma.work"}'
```

### Create a product
```
lemma connectors operations execute shopify SHOPIFY_CREATE_PRODUCT -d '{"title": "Linen shirt", "vendor": "Acme", "product_type": "Shirts"}'
```

## Tips
- `lemma connectors operations search shopify <query>` finds more operations.
- `lemma connectors operations details shopify <OPERATION>` shows the full input schema.
- An order or product id is the numeric REST id, not the `gid://shopify/...` GraphQL id.
- A connection made against the wrong store cannot be moved. Disconnect it and connect again with the right store name.
