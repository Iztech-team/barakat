"""Which chart of accounts language a company gets when nobody chose one.

ERPNext's setup wizard has no chart-language field, so the first company on a
site arrives with `custom_barakat_coa_language` empty and falls through to the
English chart — even on a site that was set up in Arabic. An Account's name is
minted at insert and cannot be changed afterwards, so that first company is the
only chance to get its books right.

The wizard does ask for a language, and frappe's "Updating global settings"
stage saves it to `System Settings.language` before ERPNext's company stage
runs. This module turns that code into a chart language.

Frappe-free on purpose: the caller reads the three inputs off frappe and does
nothing else, so the whole decision can be checked with plain unittest:

	python -m unittest barakat.chart_of_accounts.test_site_language

See docs/superpowers/specs/2026-08-01-wizard-coa-language-design.md.
"""

# The languages we hold a chart for. English is deliberately absent: a company
# with no language set gets ERPNext's own English chart, which is what we want.
TRANSLATED_LANGUAGES = ("ar", "he")


def chart_language(site_language):
	"""The chart language for a Frappe language code, or "" if we have no chart.

	A code may carry a region suffix (`ar-SA`, `he-IL`) and the chart does not
	vary by region, so only the part before the dash is considered.
	"""
	code = (site_language or "").strip().lower().split("-")[0]
	return code if code in TRANSLATED_LANGUAGES else ""


def language_for_new_company(in_setup_wizard, current_language, site_language):
	"""The language to stamp on a Company being inserted, or "" to leave it alone.

	Three ways to get "", each deliberate:

	  * not the setup wizard — the AP always sends a language of its own, and a
	    company added by hand from the desk should not silently change language
	    because of a site-wide setting;
	  * a language was already chosen — never overwrite a caller's choice;
	  * the site's language has no chart — English and everything else keep
	    ERPNext's behaviour.
	"""
	if not in_setup_wizard:
		return ""
	if (current_language or "").strip():
		return ""
	return chart_language(site_language)
