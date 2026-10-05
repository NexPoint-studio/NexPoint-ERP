"""Public compile-time target. QA staging replaces this entire module.

Never select a packaged target through environment variables or installation data.
"""

ENVIRONMENT = "production"
CHANNEL = "PROD"
PROJECT_REF = "scfncgaiovztrbgrcvkt"
ORIGIN = "https://" + PROJECT_REF + ".supabase.co"
UPDATE_CHANNEL = None
PRODUCT_DIRECTORY = "ERP"
