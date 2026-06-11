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

After installing, set one config variable on each client site:

```bash
bench --site <client-site> set-config front_door_domain "master.yourdomain.com"
```

| Variable | Description |
|---|---|
| `front_door_domain` | Hostname of the master ERPNext site. Required by the `iztechvalley_gateway` app to authenticate users and verify SSO tokens. |

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
