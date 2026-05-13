# ERPNext Multi-Site Architecture
## Full Implementation Guide — From A to Z

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [What Lives Where](#what-lives-where)
4. [One-Time Setup](#one-time-setup)
5. [Per-Client Setup](#per-client-setup)
6. [Per-User Flow — Registration](#per-user-flow--registration)
7. [Per-User Flow — Login](#per-user-flow--login)
8. [Per-User Flow — Forgot Password](#per-user-flow--forgot-password)
9. [Edge Cases](#edge-cases)
10. [Checklist](#checklist)

---

## Overview

You are building a custom frontend app (`myapp.com`) on top of ERPNext.
Your clients each have their own isolated ERPNext site. Your users log in
once with one email and one password and pick which company they want to access.

**The Players:**
- `myapp.com` — your custom frontend, the only thing users ever see
- `master.yourdomain.com` — master ERPNext site, holds all user accounts and passwords
- `client1.yourdomain.com` — client site, holds client's business data
- `client2.yourdomain.com` — another client site, fully isolated from client1

---

## Architecture

```
myapp.com (your frontend)
    │
    ├──→ master.yourdomain.com   (login, password, company list)
    │
    ├──→ client1.yourdomain.com  (business data for client1)
    │
    └──→ client2.yourdomain.com  (business data for client2)
```

**Key principles:**
- User has ONE password — lives only on the master site
- User never sees any ERPNext UI — only your custom app
- Client sites never store user passwords
- OAuth (Login with Frappe) is how client sites trust the master site
- Each client site is fully isolated from others

---

## What Lives Where

### Master Site (`master.yourdomain.com`)
- All user accounts (email + password)
- Custom DocType: `User Site Mapping` (which users belong to which sites)
- OAuth Client records (one per client site)

### Each Client Site (`clientX.yourdomain.com`)
- The client's business data (Sales Orders, Invoices, etc.)
- User accounts (no passwords — auth is handled by master site)
- Social Login Key (points to master site as OAuth provider)
- Your custom sync hook app (auto-syncs new users to master site)

### Your Frontend (`myapp.com`)
- Login screen
- Company picker screen
- Reset password screen
- OAuth callback handler (`myapp.com/oauth/callback`)
- All business UI screens

---

## One-Time Setup

> Do this once, before onboarding any client.

### 1. Create the Master Site

```bash
bench new-site master.yourdomain.com
bench --site master.yourdomain.com install-app erpnext
```

### 2. Create the `User Site Mapping` Custom DocType

On `master.yourdomain.com`, go to:
```
DocType → New DocType
  Name: User Site Mapping
  Module: (your custom module)
  Fields:
    - user         → Link → User
    - site_url     → Data  (**no trailing slash**; scheme is optional for HTTP — e.g. `pos.localhost:8000` or `https://pos.example.com`)
    - company_name → Data
    - is_active    → Check (default: 1)
```

### 3. Configure the Forgot Password URL

On `master.yourdomain.com`, go to:
```
Settings → System Settings
  Forgot Password URL: https://myapp.com/reset-password
```

ERPNext will append `?key=abc123` automatically to this URL.

### 4. Build Your Custom Sync Hook App

Create a small Frappe app that fires whenever a new user is created
on any client site. It should:

1. Listen to the `on_after_insert` hook on the `User` DocType
2. Call the master site API to create the same user there
3. Trigger the welcome email from the master site

```python
# hooks.py in your custom app
doc_events = {
    "User": {
        "after_insert": "your_app.sync.create_user_on_master"
    }
}
```

```python
# sync.py in your custom app
import frappe
import requests

def create_user_on_master(doc, method):
    master_url = "https://master.yourdomain.com"
    api_key = frappe.conf.master_api_key
    api_secret = frappe.conf.master_api_secret

    requests.post(
        f"{master_url}/api/resource/User",
        headers={
            "Authorization": f"token {api_key}:{api_secret}",
            "Content-Type": "application/json"
        },
        json={
            "email": doc.email,
            "first_name": doc.first_name,
            "last_name": doc.last_name,
            "send_welcome_email": 1
        }
    )
```

### 5. Build Your Frontend Screens

Pages to build on `myapp.com`:

| Route | Screen |
|---|---|
| `/login` | Email + password form |
| `/pick-company` | Company picker |
| `/oauth/callback` | OAuth handler (no UI, just logic) |
| `/reset-password` | Set new password form |
| `/forgot-password` | Request reset email form |
| `/dashboard` | Main app (after login) |

---

## Per-Client Setup

> Do this every time you onboard a new client. Takes ~5 minutes.

### Step 1 — Create the Client Site

```bash
bench new-site client1.yourdomain.com
bench --site client1.yourdomain.com install-app erpnext
bench --site client1.yourdomain.com install-app your_sync_app
```

### Step 2 — Configure the Sync App

In `client1.yourdomain.com/site_config.json`, add:
```json
{
  "master_api_key": "your_master_site_api_key",
  "master_api_secret": "your_master_site_api_secret"
}
```

### Step 3 — Create the OAuth Client on the Master Site

On `master.yourdomain.com`, go to:
```
OAuth Client → New
  App Name:            Client1
  Skip Authorization:  ✅ (checked)
  Redirect URI:        https://myapp.com/oauth/callback
  Default Redirect URI: https://myapp.com/oauth/callback
→ Save
→ Copy the generated: Client ID and Client Secret
```

### Step 4 — Create the Social Login Key on the Client Site

On `client1.yourdomain.com`, go to:
```
Social Login Key → New
  Social Login Provider: Frappe
  Client ID:             (paste from step 3)
  Client Secret:         (paste from step 3)
  Base URL:              https://master.yourdomain.com
→ Save
```

### Step 5 — Add Client to User Site Mapping

On `master.yourdomain.com`, go to:
```
User Site Mapping → New
  Site URL:      client1.yourdomain.com
  Company Name:  Client1 Co
  Is Active:     ✅
```

> Client is now fully set up. Repeat steps 1–5 for every new client.

---

## Per-User Flow — Registration

> This happens automatically when Client1 adds a new employee (Ahmed) to their ERPNext site.

```
CLIENT1 ADMIN (on client1.yourdomain.com)
    |
    | Goes to: User → New User
    | Fills in:
    |   Email: ahmed@gmail.com
    |   First Name: Ahmed
    |   Role: Sales User
    | → Saves
    |
    ↓
client1.yourdomain.com
    |
    | Your sync hook fires automatically
    |
    |----[POST]──────────────────────────────→ master.yourdomain.com
    |    /api/resource/User
    |    Headers:
    |      Authorization: token apikey:apisecret
    |    Body:
    |      {
    |        "email": "ahmed@gmail.com",
    |        "first_name": "Ahmed",
    |        "send_welcome_email": 1
    |      }
    |                                          master.yourdomain.com
    |                                              |
    |←---[201 Created]──────────────────────←     | Creates Ahmed's account
    |    { "name": "ahmed@gmail.com" }            |
    |                                              |
    |                                         master.yourdomain.com
    |                                              |
    |                                              |----[EMAIL]──→ ahmed@gmail.com
    |                                                   Subject: Set your password
    |                                                   Link: https://myapp.com/
    |                                                         reset-password?key=abc123

AHMED
    |
    | Gets the email
    | Clicks the link
    | Lands on myapp.com/reset-password?key=abc123
    | Types his new password
    |
    |----[POST]──────────────────────────────→ master.yourdomain.com
         /api/method/frappe.core.doctype
         .user.user.update_password
         Body:
           {
             "key": "abc123",
             "new_password": "hisChosenPassword"
           }
                                               master.yourdomain.com
                                                   |
         [200 OK] ←────────────────────────←      | Updates password
         { "message": "Password Updated" }
         
    myapp.com shows: "Password set! You can now login."
```

**Result:** Ahmed has one account on the master site with one password. He has a matching account on client1 with his roles. He has never touched ERPNext's UI.

---

## Per-User Flow — Login

> Ahmed opens myapp.com and logs in.

```
AHMED
    |
    | Opens myapp.com/login
    | Types: ahmed@gmail.com / hisChosenPassword
    | Clicks Login
    |
    |----[POST]──────────────────────────────→ master.yourdomain.com
         /api/method/login
         Body:
           {
             "usr": "ahmed@gmail.com",
             "pwd": "hisChosenPassword"
           }
                                               master.yourdomain.com
                                                   |
                                                   | Validates credentials
                                                   |
    ←---[200 OK + session cookie]────────────←     |
         {
           "message": "Logged In",
           "full_name": "Ahmed"
         }
         Cookie: sid=master_session_xxxx
    |
    | Stores master session cookie
    |
    |----[GET]───────────────────────────────→ master.yourdomain.com
         /api/resource/User Site Mapping
         ?filters=[
           ["user","=","ahmed@gmail.com"],
           ["is_active","=",1]
         ]
         Cookie: sid=master_session_xxxx
                                               master.yourdomain.com
                                                   |
                                                   | Fetches Ahmed's companies
                                                   |
    ←---[200 OK]─────────────────────────────←    |
         {
           "data": [
             {
               "site_url": "client1.yourdomain.com",  // bare hostname → http:// assumed; use https://client1.yourdomain.com for TLS
               "company_name": "Client1 Co"
             }
           ]
         }
    |
    | myapp.com/pick-company shows:
    |   ● Client1 Co
    |
    | Ahmed picks: Client1 Co
    |
    | ── OAUTH STARTS ──────────────────────────────────────────
    |
    |----[BROWSER REDIRECT]──────────────────→ master.yourdomain.com
         /api/method/frappe.integrations
         .oauth2.authorize
         ?client_id=CLIENT1_OAUTH_ID
         &redirect_uri=https://myapp.com/oauth/callback
         &response_type=code
         &scope=openid
                                               master.yourdomain.com
                                                   |
                                                   | Ahmed is already logged in
                                                   | No login screen shown
                                                   | Instantly issues auth code
                                                   |
    ←---[302 REDIRECT]───────────────────────←    |
         Location: https://myapp.com/oauth/callback?code=xyz789
    |
    | Browser lands on myapp.com/oauth/callback
    | App reads: code=xyz789 from URL
    |
    |----[POST]──────────────────────────────→ master.yourdomain.com
         /api/method/frappe.integrations
         .oauth2.get_token
         Body:
           {
             "code": "xyz789",
             "client_id": "CLIENT1_OAUTH_ID",
             "client_secret": "CLIENT1_OAUTH_SECRET",
             "redirect_uri": "https://myapp.com/oauth/callback",
             "grant_type": "authorization_code"
           }
                                               master.yourdomain.com
                                                   |
                                                   | Validates code
                                                   | Issues access token
                                                   |
    ←---[200 OK]─────────────────────────────←    |
         {
           "access_token": "abc123",
           "expires_in": 3600,
           "token_type": "Bearer"
         }
    |
    |----[GET]───────────────────────────────→ client1.yourdomain.com
         /api/method/frappe.integrations
         .oauth2_logins.login_via_frappe
         ?access_token=abc123
                                               client1.yourdomain.com
                                                   |
                                                   | Calls master site
                                                   | to verify token
                                                   |
                                           ←──→ master.yourdomain.com
                                                   |
                                                   | Token valid ✅
                                                   | Ahmed exists ✅
                                                   | Creates session
                                                   |
    ←---[200 OK + session cookie]────────────←    |
         Cookie: sid=client1_ahmed_session_xxxx
    |
    | ── OAUTH ENDS ───────────────────────────────────────────
    |
    | Ahmed is now logged in to client1 via myapp.com
    | myapp.com/dashboard loads
    |
    |----[GET]───────────────────────────────→ client1.yourdomain.com
         /api/resource/Sales Order
         Cookie: sid=client1_ahmed_session_xxxx
                                               client1.yourdomain.com
                                                   |
                                                   | Checks Ahmed's permissions
                                                   | Returns his data
                                                   |
    ←---[200 OK]─────────────────────────────←    |
         { "data": [ ...sales orders... ] }
    |
    | Ahmed sees his dashboard on myapp.com ✅
```

---

## Per-User Flow — Forgot Password

> Ahmed forgot his password and needs to reset it.

```
AHMED
    |
    | Opens myapp.com/login
    | Clicks "Forgot Password?"
    | Lands on myapp.com/forgot-password
    | Types: ahmed@gmail.com
    | Clicks Send Reset Email
    |
    |----[POST]──────────────────────────────→ master.yourdomain.com
         /api/method/frappe.core.doctype
         .user.user.reset_password
         Body:
           {
             "user": "ahmed@gmail.com"
           }
                                               master.yourdomain.com
                                                   |
                                                   | Generates reset key
                                                   | Sends reset email
                                                   |
    ←---[200 OK]─────────────────────────────←    |
         { "message": "Password reset mail sent" }
    |                                              |
    |                                              |----[EMAIL]──→ ahmed@gmail.com
    |                                                   Subject: Reset your password
    |                                                   Link: https://myapp.com/
    |                                                         reset-password?key=newkey123
    |
    | myapp.com shows: "Check your email for a reset link."
    |
    | ─────────────────────────────────────────────────────────
    |
    | Ahmed gets the email
    | Clicks the link
    | Lands on myapp.com/reset-password?key=newkey123
    | Types new password
    | Clicks Reset Password
    |
    |----[POST]──────────────────────────────→ master.yourdomain.com
         /api/method/frappe.core.doctype
         .user.user.update_password
         Body:
           {
             "key": "newkey123",
             "new_password": "hisNewPassword"
           }
                                               master.yourdomain.com
                                                   |
                                                   | Updates password ✅
                                                   | Invalidates the key
                                                   |
    ←---[200 OK]─────────────────────────────←    |
         { "message": "Password Updated" }

    myapp.com shows: "Password updated! Please login."
    Redirects Ahmed to myapp.com/login
```

**Important:** Only the master site password changes.
Client sites are completely unaffected because they don't store passwords.

---

## Edge Cases

| Situation | What happens | How to handle |
|---|---|---|
| Wrong password at login | Master site returns 401 | Show "Invalid email or password" on login screen |
| 5 consecutive wrong passwords | Track count on your frontend or master site | Lock for 15 mins, show "Too many attempts" |
| Email doesn't exist | Master site returns success anyway | Always show "If this email exists, a reset link was sent" (security best practice) |
| Reset link clicked twice | Master site returns error on second use | Show "This link has already been used. Request a new one." |
| Reset link expired | Master site returns error | Show "Link expired. Request a new one." |
| User has no companies mapped | User Site Mapping returns empty list | Show "You have no companies assigned. Contact your administrator." |
| Client site is down | `login_via_frappe` call fails | Show that company as grayed out in picker with "Currently unavailable" |
| User removed from client site | Their account disabled on client1 | `login_via_frappe` fails → show "You no longer have access to this company" |
| User exists on master but not on client | `login_via_frappe` fails | Show "Access not configured. Contact your administrator." |
| OAuth code expired | `get_token` call fails | Restart the OAuth flow, redirect user back to pick-company |
| User switches companies | | Clear client1 session cookie, restart OAuth flow for new company |

---

## Checklist

### One-Time Setup
- [ ] Create master ERPNext site
- [ ] Create `User Site Mapping` custom DocType on master site
- [ ] Set Forgot Password URL to `https://myapp.com/reset-password` on master site
- [ ] Create a master site API key for your sync hook to use
- [ ] Build the user sync hook app
- [ ] Build `myapp.com` with all required screens:
  - [ ] `/login`
  - [ ] `/pick-company`
  - [ ] `/oauth/callback`
  - [ ] `/forgot-password`
  - [ ] `/reset-password`
  - [ ] `/dashboard`

### Per-Client Setup (repeat for every new client)
- [ ] Create client site with `bench new-site`
- [ ] Install ERPNext on client site
- [ ] Install your sync hook app on client site
- [ ] Add master site API credentials to client site config
- [ ] Create OAuth Client on master site → copy Client ID + Secret
- [ ] Create Social Login Key on client site using those credentials
- [ ] Add client to `User Site Mapping` DocType on master site

### Per-User (automated — no manual work needed)
- [ ] Client admin creates user in their ERPNext site (normal flow)
- [ ] Sync hook auto-creates user on master site ✅
- [ ] Master site sends welcome email to user ✅
- [ ] User sets their password via the link ✅
- [ ] User can now log in to myapp.com ✅

---

## Flow Summary Table

| Step | From | To | What happens |
|---|---|---|---|
| Create user | Client site hook | Master site API | Ahmed's account created on master |
| Welcome email | Master site | Ahmed's inbox | Link to set password |
| Set password | Ahmed → myapp.com | Master site API | Ahmed sets his one password |
| Login | Ahmed → myapp.com | Master site API | Credentials validated |
| Company list | myapp.com | Master site API | Ahmed's accessible sites fetched |
| OAuth redirect | myapp.com | Master site | Auth code issued |
| Token exchange | myapp.com | Master site API | Access token received |
| Session creation | myapp.com | Client site API | Ahmed's session on client1 created |
| Data calls | myapp.com | Client site API | Ahmed's actual business data |
| Forgot password | Ahmed → myapp.com | Master site API | Reset email triggered |
| Reset password | Ahmed → myapp.com | Master site API | Password updated |
