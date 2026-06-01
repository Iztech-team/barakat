# Barakat — ERPNext Custom App Documentation

## Table of Contents

1. [What This App Is](#what-this-app-is)
2. [How It Fits In the System](#how-it-fits-in-the-system)
3. [Installation](#installation)
4. [Custom DocTypes](#custom-doctypes)
5. [Custom Fields on Standard DocTypes](#custom-fields-on-standard-doctypes)
6. [API Endpoints](#api-endpoints)
7. [DocType Overrides](#doctype-overrides)
8. [Validations](#validations)
9. [Document Events (Hooks)](#document-events-hooks)Dimensions
10. [Setup — After Install](#setup--after-install)
11. [Frontend JS](#frontend-js)
12. [Fixtures](#fixtures)
13. [Multi-Site Architecture Reference](#multi-site-architecture-reference)

---

## What This App Is

`barakat` is a Frappe/ERPNext custom app that extends standard ERPNext to support a **multi-device, multi-branch, offline-first POS system**. It does not replace ERPNext — it adds on top of it.

The app's job is to:

- Add new DocTypes (`Device`, `Branch POS Profile`) that the desktop POS app depends on
- Add custom fields to standard ERPNext DocTypes (Branch, Employee, POS Profile, POS Opening/Closing Entry, POS Invoice, Sales Invoice, Journal Entry)
- Expose whitelisted API endpoints the desktop app calls to register devices, pick POS profiles, and check profile assignments
- Override POS Invoice and POS Opening Entry validation to support **offline-first** order submission
- Enforce business rules: unique employee PINs per company, unique POS profile per branch, one open shift per device per branch

This app is installed on every **client site** (the ERPNext instance that holds actual business data). It is not installed on the master site.

---

## How It Fits In the System

The full system has three layers:

```
Desktop POS App (Electrobun / Bun + React)
    │
    ├──→ master site (iztechvalley_gateway or similar)
    │       Handles: user accounts, passwords, site-to-user mapping
    │
    └──→ client site (this app is installed here)
            Handles: actual business data — items, customers, invoices, shifts
            barakat app extends this site
```

The desktop app talks **directly** to the client site for all POS operations (submitting invoices, opening/closing shifts, fetching items and customers). The master site is only used during login to authenticate the user and get the list of client sites they can access.

---

## Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch main
bench --site <client-site> install-app barakat
```

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch main
bench --site <client-site> install-app barakat
bench --site <client-site> set-config front_door_domain "master.yourdomain.com"
```

| Variable | Description |
|---|---|
| `front_door_domain` | Hostname of the master ERPNext site. Required by the `iztechvalley_gateway` app to authenticate users and verify SSO tokens. |

After install, the `after_install` hook runs automatically and sets up everything else the app needs (see [Setup — After Install](#setup--after-install)).

---

## Custom DocTypes

### `Device`

Represents a physical POS terminal (cash register). Each device is identified by its MAC address.

**Fields:**

| Field | Type | Description |
|---|---|---|
| `device_id` | Data (PK) | Machine's MAC address, set on first app launch |
| `device_name` | Data | Human-readable name assigned by the operator (e.g. "Register 1") |

**Purpose:** Acts as the registry of known POS machines. When the desktop app launches for the first time on a machine, it calls `register_device` to create this record. The `device_id` is then used to link a POS Profile to that specific machine (via `custom_device` on POS Profile).

---

### `Branch POS Profile`

A child table DocType that lives inside the Branch DocType. Each row links one POS Profile to a branch.

**Fields:**

| Field | Type | Description |
|---|---|---|
| `pos_profile` | Link → POS Profile | The POS Profile assigned to this branch |

**Purpose:** Defines which POS Profiles are available in a given branch. When the desktop app asks for available profiles (`get_available_profiles`), it reads from this table. A profile can only belong to **one branch** — enforced by `validate_branch`.

---

## Custom Fields on Standard DocTypes

These fields are added to standard ERPNext DocTypes by this app. They are exported as fixtures (`fixtures/custom_field.json`) and created on install via `setup/install.py`.

### Branch

| Field | Type | Description |
|---|---|---|
| `custom_pos_company` | Link → Company | Which company this branch belongs to for POS purposes |
| `custom_pos_profiles` | Table → Branch POS Profile | List of POS Profiles assigned to this branch |

### POS Profile

| Field | Type | Description |
|---|---|---|
| `custom_device` | Link → Device | Which physical device is currently using this profile (set by `select_profile`) |
| `custom_branch` | Link → Branch (read-only) | Which branch this profile belongs to (auto-set by Branch's validate hook) |
| `custom_cash_account` | Link → Account | POS cash account override (used by desktop app config) |
| `custom_counter_account` | Link → Account | POS counter account override (used by desktop app config) |

### Employee

| Field | Type | Description |
|---|---|---|
| `custom_pos_pin` | Data (max 6 chars) | 4–6 digit PIN used to punch in on the POS |
| `custom_role_preset` | Link → Role | POS role (manager / supervisor / cashier / etc.) |
| `custom_pos_branches` | Table → POS Employee Branch | Branches this employee is allowed to work in |

### POS Invoice

| Field | Type | Description |
|---|---|---|
| `custom_external_id` | Data (unique) | UUID assigned by the desktop app. Prevents duplicates during offline sync retries. |
| `custom_operator_employee` | Link → Employee | The cashier who processed this invoice |

### Sales Invoice

| Field | Type | Description |
|---|---|---|
| `custom_external_id` | Data (unique) | Same as above, for Sales Invoices |
| `custom_operator_employee` | Link → Employee | The cashier who processed this invoice |

### Journal Entry

| Field | Type | Description |
|---|---|---|
| `custom_external_id` | Data (unique) | UUID for idempotent sync of cash movement journal entries |
| `custom_pos_opening_entry` | Link → POS Opening Entry | Links this journal entry back to the shift it belongs to |

### POS Opening Entry

| Field | Type | Description |
|---|---|---|
| `custom_device_id` | Data | The device ID (MAC address) that opened this shift |
| `custom_opened_by_staff` | Link → Employee | The staff member who opened the shift |

### POS Closing Entry

| Field | Type | Description |
|---|---|---|
| `custom_device_id` | Data | The device ID that closed this shift |
| `custom_closed_by_staff` | Link → Employee | The staff member who closed the shift |

---

## API Endpoints

All endpoints are in `barakat/api/device.py` and are decorated with `@frappe.whitelist()`, meaning they require an authenticated session and are callable from the desktop app via the ERPNext REST API at `/api/method/barakat.api.device.<function_name>`.

---

### `register_device(device_id, device_name)`

**Called when:** The desktop app launches for the first time on a new machine.

**What it does:**
- Checks if a `Device` record with the given `device_id` already exists
- If not, creates a new `Device` record with the given `device_name`
- Returns the device record and whether it was newly created

**Returns:**
```json
{
  "device": { "device_id": "...", "device_name": "Register 1" },
  "is_new": true
}
```

---

### `get_available_profiles(branch, device_id)`

**Called when:** The user selects a branch in the desktop app. The app needs to show which POS Profiles are available for this device to claim.

**What it does:**
1. Reads all POS Profiles listed in the branch's `custom_pos_profiles` child table
2. Filters to only those that are either:
   - Not linked to any device (`custom_device` is null) — available to claim
   - Already linked to **this** device (`custom_device == device_id`) — can be re-selected
3. Returns the filtered list with a flag indicating which one the device already owns

**Returns:**
```json
[
  { "pos_profile": "POS-BRANCH1-1", "is_mine": false },
  { "pos_profile": "POS-BRANCH1-2", "is_mine": true }
]
```

A profile linked to a **different** device is excluded entirely — it is not available.

---

### `select_profile(device_id, pos_profile)`

**Called when:** The user picks a POS Profile in the desktop app.

**What it does:**
1. Checks that the chosen profile is not already linked to a **different** device — throws `PermissionError` if it is
2. If this device previously held a different profile, clears `custom_device` on the old profile
3. Sets `custom_device = device_id` on the chosen profile

This is a **1-to-1 exclusive link** — one device owns one profile at a time, and one profile can only be owned by one device.

**Returns:**
```json
{ "ok": true, "pos_profile": "POS-BRANCH1-2" }
```

---

### `check_device_profile(device_id, pos_profile)`

**Called when:** The user selects a branch (on every branch selection, not just first time). Used to detect if an admin changed the device-profile mapping externally.

**What it does:**
- Reads `custom_device` from the given POS Profile
- Compares it against `device_id`

**Returns one of three statuses:**

| Status | Meaning |
|---|---|
| `"ok"` | Profile is still linked to this device — all good, proceed |
| `"changed"` | Profile was reassigned to a different device by an admin |
| `"unlinked"` | Profile was unlinked entirely by an admin |

The desktop app reacts to `"changed"` or `"unlinked"` by clearing its locally stored profile and sending the user back to the profile picker.

---

## DocType Overrides

### `BarakatPOSInvoice` — overrides `POS Invoice`

**File:** `barakat/overrides/pos_invoice.py`

Overrides the `validate_pos_opening_entry` method to replace the standard ERPNext check (which rejects invoices not from today) with a more permissive **offline-first** rule.

**Standard ERPNext behavior:** Rejects invoices if `posting_date != today`. This breaks offline POS — orders created on Day 1 that sync on Day 2 would all fail.

**Barakat behavior:** Allows any invoice as long as `posting_date >= shift.period_start_date`. The only thing that gets rejected is an invoice dated **before** the shift opened, which would be a genuine data error (not an offline sync artifact).

**Also validates:**
- Exactly one open POS Opening Entry must exist for the profile (throws if zero or more than one)

---

### `BarakatPOSOpeningEntry` — overrides `POS Opening Entry`

**File:** `barakat/overrides/pos_opening_entry.py`

Adds one check to the standard `validate` method: `check_device_open_shift`.

**What it does:** Before allowing a new shift to open, checks whether the same device already has an **open** POS Opening Entry for the same POS Profile (branch). If yes, throws an error.

```
"This device already has an open POS session for this branch.
 Close it before opening a new one."
```

**Scope of the check:** Scoped to `(device_id, pos_profile)` — a device can have one open shift per branch, not globally. This allows the same physical device to service different branches if needed (rare but supported).

---

## Validations

### `validate_employee_pin` — on Employee save

**File:** `barakat/validations.py`  
**Triggered by:** `doc_events["Employee"]["validate"]` hook

Enforces two rules on `custom_pos_pin`:

1. **Format:** Must be 4–6 digits only (no letters, spaces, or special characters). Uses `re.fullmatch(r"\d{4,6}", pin)`.

2. **Uniqueness per company:** No two employees in the same company can share a PIN. The company is resolved by following `Employee.branch → Branch.custom_pos_company`. The uniqueness check runs a raw SQL query across all employees in the same company.

If either check fails, `frappe.throw()` is called with a descriptive error message displayed to the admin in the ERPNext UI.

---

### `validate_branch` — on Branch save

**File:** `barakat/overrides/branch.py`  
**Triggered by:** `doc_events["Branch"]["validate"]` hook

Runs three checks and one sync action:

1. **`_validate_unique_pos_profiles`:** Each POS Profile can appear only once in the branch's `custom_pos_profiles` table. Duplicates throw an error.

2. **`_validate_profiles_not_in_other_branches`:** A POS Profile can only belong to one branch. If any profile in the table is already listed in a different branch, throws an error.

3. **`_sync_branch_back_reference`:** Maintains the `custom_branch` field on each POS Profile automatically:
   - Profiles now in this branch → `custom_branch` set to this branch
   - Profiles removed from this branch → `custom_branch` cleared

This ensures `POS Profile.custom_branch` is always accurate without requiring admins to set it manually.

---

## Document Events (Hooks)

Defined in `hooks.py`:

```python
doc_events = {
    "Employee": {
        "validate": "barakat.validations.validate_employee_pin",
    },
    "Branch": {
        "validate": "barakat.overrides.branch.validate_branch",
    },
}
```

```python
override_doctype_class = {
    "POS Opening Entry": "barakat.overrides.pos_opening_entry.BarakatPOSOpeningEntry",
    "POS Invoice":       "barakat.overrides.pos_invoice.BarakatPOSInvoice",
}
```

```python
doctype_js = {
    "Employee": "public/js/employee.js",
}
```

---

## Setup — After Install

**File:** `barakat/setup/install.py`  
**Triggered by:** `after_install = "barakat.setup.install.after_install"` in hooks.py

Runs automatically when you run `bench install-app barakat`. Executes these steps in order, logging errors without stopping if one step fails:

### 1. `_enable_negative_stock`
Sets `Stock Settings.allow_negative_stock = 1`.

Needed because POS sales happen offline and stock levels may temporarily go negative before the sync catches up.

### 2. `_set_pos_invoice_type`
Sets `POS Settings.invoice_type = "POS Invoice"`.

Ensures the system uses POS Invoices (not Sales Invoices) for POS transactions.

### 3. `_set_session_expiry`
Sets `System Settings.session_expiry = "8760:00"` (1 year).

Prevents the ERPNext session from expiring while the POS app is running overnight. The desktop app manages its own session state and doesn't handle ERPNext session timeouts well.

### 4. `_create_misc_item`
Creates a generic `MISC` item in a `Miscellaneous` item group.

Used for **ad-hoc line items** — when a cashier needs to add a custom price/product that isn't in the catalog (walk-in pricing, one-off items, etc.). The desktop app always has this item available for manual entry.

### 5. `_create_default_customer`
Creates a `Default Customer` record using the first available customer group and territory.

Used as the customer on POS invoices when no specific customer is selected (anonymous sale). The desktop app sets this customer automatically for walk-in sales.

### 6. `_create_device_custom_fields`
Creates three custom fields that are required for the device-profile linking system:

- `Branch.custom_pos_profiles` (Table → Branch POS Profile)
- `POS Profile.custom_device` (Link → Device)
- `POS Profile.custom_branch` (Link → Branch, read-only)

These are also in `fixtures/custom_field.json` for export, but this function creates them on fresh installs before the fixture import runs.

---

## Frontend JS

**File:** `barakat/public/js/employee.js`  
**Loaded on:** Employee form in ERPNext desk UI

Enhances the `custom_pos_pin` field in the Employee form with two behaviors:

1. **Real-time input sanitization:** Strips any non-digit characters immediately on every keystroke using a native `input` event listener. The character never visibly appears — it is removed before the browser even renders it. Also enforces the 6-character maximum.

2. **Inline validation hint:** When the PIN field changes and the value is between 1–3 digits, shows the hint `"PIN must be 4 to 6 digits."` below the field. Once the PIN reaches 4+ digits, shows the neutral hint `"4 to 6 digits only."`.

This gives admins immediate feedback while setting employee PINs, before the server-side validation in `validate_employee_pin` runs on save.

---

## Fixtures

**`fixtures/custom_field.json`** — Exports all custom fields as a fixture so they can be transferred between ERPNext instances using `bench export-fixtures` / `bench import-fixtures`. Contains:

| DocType | Fieldname | Purpose |
|---|---|---|
| Branch | `custom_pos_company` | Company for POS |
| Branch | `custom_pos_profiles` | Table of POS Profiles |
| POS Profile | `custom_device` | Linked device |
| POS Profile | `custom_branch` | Owning branch |
| POS Profile | `custom_cash_account` | Cash account |
| POS Profile | `custom_counter_account` | Counter account |
| Employee | `custom_pos_pin` | Cashier PIN |
| Employee | `custom_role_preset` | POS role |
| Employee | `custom_pos_branches` | Allowed branches |
| POS Invoice | `custom_external_id` | UUID from desktop app |
| POS Invoice | `custom_operator_employee` | Cashier who processed |
| Sales Invoice | `custom_external_id` | UUID from desktop app |
| Sales Invoice | `custom_operator_employee` | Cashier who processed |
| Journal Entry | `custom_external_id` | UUID for cash movements |
| Journal Entry | `custom_pos_opening_entry` | Linked shift |
| POS Opening Entry | `custom_device_id` | Device that opened shift |
| POS Opening Entry | `custom_opened_by_staff` | Staff who opened shift |
| POS Closing Entry | `custom_device_id` | Device that closed shift |
| POS Closing Entry | `custom_closed_by_staff` | Staff who closed shift |

**`fixtures/doctype.json`** — Exports the custom DocTypes (`Device`, `Branch POS Profile`) so they can be imported into another ERPNext instance.

The `hooks.py` fixtures block defines which records to export:
```python
fixtures = [
    {
        "dt": "DocType",
        "filters": [["name", "in", ["POS Employee Branch", "Device", "Branch POS Profile"]]],
    },
    {
        "dt": "Custom Field",
        "filters": [["fieldname", "in", [...all custom field names...]]],
    },
]
```

---

## Multi-Site Architecture Reference

This section documents how the full Barakat system is architected, from a single user login to data flowing into a client ERPNext site.

### Players

| Player | What it is |
|---|---|
| Desktop POS App | The Electrobun desktop application, runs on Windows |
| Master site | Central ERPNext instance — stores user accounts, passwords, and which users have access to which client sites |
| Client site | One ERPNext instance per business — stores items, customers, invoices, shifts. **Barakat app is installed here.** |

### Authentication Flow (how the desktop app logs in)

The desktop app uses a **credential-based SSO flow** (not standard OAuth browser redirect, since it's a desktop app):

```
1. User types email + password in the desktop app
       ↓
2. Desktop app → POST /api/method/login → master site
   Response: master session SID + user's full name
       ↓
3. Desktop app → GET /api/resource/User Site Mapping → master site
   Response: list of { site_url, company_name } the user can access
       ↓
4. User picks a site from the list
       ↓
5. Desktop app → POST verify_user_credentials → master site
   Body: { user, password, site_url }
   Response: one-time SSO token
       ↓
6. Desktop app → GET sso_login?token=... → client site
   Client site validates token with master site
   Response: client site session SID (Set-Cookie)
       ↓
7. Desktop app uses client SID for all subsequent POS API calls
```

**Key design decisions:**
- The user's password is kept in memory only for the duration of site selection. It is never written to disk.
- Client sites never store user passwords — authentication always goes through the master site.
- One password works across all client sites the user has access to.

### Device Registration Flow (first launch on a new machine)

```
1. Desktop app generates device_id from MAC address of primary NIC
2. Checks device-names.json for a saved name for this site
3. If no name saved → calls GET /api/resource/Device/{device_id} → client site
   - If found: saves name locally, proceeds
   - If not found: shows "Name this device" dialog to the user
4. User enters a name (e.g. "Main Register")
5. Desktop app → POST /api/method/barakat.api.device.register_device → client site
   Body: { device_id, device_name }
6. Device record created in ERPNext
7. Name saved in device-names.json on local machine (survives logouts)
```

### POS Profile Selection Flow (on each branch selection)

```
1. User selects a branch in the desktop app
2. Desktop app → GET /api/method/barakat.api.device.get_available_profiles
   Params: { branch, device_id }
   Response: list of profiles (available + already mine)
3. If the device already has a profile (is_mine: true) → auto-select it
4. If not → show profile picker to user
5. User picks a profile
6. Desktop app → POST /api/method/barakat.api.device.select_profile
   Body: { device_id, pos_profile }
7. Profile linked to device in ERPNext
8. Desktop app saves selected profile to device-names.json
9. Desktop app fetches profile data (cash account, counter account, warehouse, cost center, price list)
   and caches it locally
```

On every subsequent branch selection (not just first time), the desktop app calls `check_device_profile` to detect if an admin changed the mapping while the app was running.

### Shift (POS Opening Entry) Flow

```
Open shift:
  Desktop app → POST /api/resource/POS Opening Entry → client site
  Body includes: pos_profile, user, custom_device_id, custom_opened_by_staff, opening_details
  BarakatPOSOpeningEntry.validate runs → check_device_open_shift → blocks if already open

Submit orders (offline):
  Orders stored locally in SQLite
  Background sync pushes them → POST /api/resource/POS Invoice → client site
  BarakatPOSInvoice.validate_pos_opening_entry runs → allows if posting_date >= shift start date

Close shift:
  Desktop app → POST /api/resource/POS Closing Entry → client site
  Body includes: pos_opening_entry, closing_details, custom_device_id, custom_closed_by_staff
```

### Data Sync Flow

The desktop app maintains a local SQLite database (via Drizzle ORM). It syncs in two directions:

**Pull (ERPNext → local):** Items, customers, branches, companies, and staff are pulled from the client site on a schedule (every 30–60 seconds). The app uses `modified` timestamps and checkpoints to do incremental pulls.

**Push (local → ERPNext):** Orders and cash movements are submitted to ERPNext as soon as they are created (or when network is restored). Each record has a status (`pending → syncing → synced / failed`) and retries automatically.

The `custom_external_id` field on POS Invoice and Journal Entry is the desktop app's UUID for each record. It is marked `unique` in ERPNext, so retrying the same order after a timeout never creates a duplicate — ERPNext will reject the second insert with a unique constraint violation, which the desktop app interprets as "already synced."

### User Registration Flow (how new employees get access)

```
1. Admin creates a User in the client site's ERPNext
2. The iztechvalley_gateway app (installed on client sites) fires a hook
   → Creates the same user on the master site
   → Master site sends "Set your password" email to the user
3. User clicks the link, sets their password on the master site
4. User can now log in to the desktop POS app
```

The barakat app itself does not handle user sync — that is handled by a separate `iztechvalley_gateway` app. The barakat app only handles device management, POS profile assignment, and POS validation overrides.

---

## File Structure Reference

```
barakat/
├── barakat/
│   ├── api/
│   │   └── device.py              # Whitelisted endpoints: register_device,
│   │                              # get_available_profiles, select_profile,
│   │                              # check_device_profile
│   ├── barakat/
│   │   └── doctype/
│   │       ├── device/            # Device DocType (device_id + device_name)
│   │       └── branch_pos_profile/# Child table for Branch → POS Profile links
│   ├── config/                    # Frappe app config
│   ├── fixtures/
│   │   ├── custom_field.json      # All custom fields (exported for migration)
│   │   └── doctype.json           # Custom DocTypes (exported for migration)
│   ├── overrides/
│   │   ├── branch.py              # Branch validate: profile uniqueness + back-ref sync
│   │   ├── pos_invoice.py         # POS Invoice: offline-first posting date validation
│   │   └── pos_opening_entry.py   # POS Opening Entry: block duplicate open shifts
│   ├── public/
│   │   └── js/
│   │       └── employee.js        # Employee form: PIN field sanitization + hints
│   ├── setup/
│   │   └── install.py             # after_install: settings, MISC item, default customer,
│   │                              # custom fields
│   ├── templates/                 # (empty — no web templates)
│   ├── www/                       # (removed — auth handled by gateway app)
│   ├── hooks.py                   # App registration, doc_events, overrides, fixtures
│   ├── modules.txt                # Module name: Barakat
│   ├── patches.txt                # (no patches yet)
│   └── validations.py             # Employee PIN: format + uniqueness per company
├── erpnext-multisite-architecture.md  # Architecture reference guide
├── pyproject.toml                 # Python package metadata
├── README.md                      # Quick install guide
└── DOCS.md                        # This file
```
