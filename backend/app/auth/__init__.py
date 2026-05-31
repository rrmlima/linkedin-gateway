# backend/app/auth/__init__.py
# Keep package import side-effect free.
#
# Route modules are imported explicitly by the FastAPI app entrypoint,
# so importing app.auth.* helpers in unit tests should not pull in OAuth
# or other heavy dependencies unnecessarily.
