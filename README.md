### Barakat

Barakat custom ERP configuration and extensions

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch main
bench install-app barakat
```

### Site Configuration

After installing on each client site, add the following 3 variables to the site config:

```bash
bench --site <client-site> set-config master_url "http://master.localhost:8000"
bench --site <client-site> set-config master_api_key "your_master_api_key"
bench --site <client-site> set-config master_api_secret "your_master_api_secret"
```

| Variable | Description |
|---|---|
| `master_url` | Full URL of the master ERPNext site |
| `master_api_key` | API key from the master site service account |
| `master_api_secret` | API secret from the master site service account |

The API key and secret can be generated on the master site under **Settings → Users → (service user) → API Access → Generate Keys**.

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/barakat
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
