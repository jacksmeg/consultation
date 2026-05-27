CONSULTATION ONLINE DEPLOY PACKAGE
=================================

Folder: consultation_online_package

This package is standalone for hosting consultation.jhimssoftware.com online without running local .bat files.

Included:
- consultation_portal_app.py
- templates/consultation*.html
- static assets needed by the consultation UI
- requirements.txt
- Procfile
- render.yaml

Recommended host: Render (Web Service + Persistent Disk)

Why disk is needed:
- This app uses SQLite (consultation_portal.db).
- On cloud platforms, local filesystem is usually ephemeral unless you attach persistent storage.

Render deploy steps:
1. Push this folder to a GitHub repo (or use Render Blueprint with render.yaml).
2. In Render: New -> Web Service (or Blueprint).
3. Build command: pip install -r requirements.txt
4. Start command: python consultation_portal_app.py
5. Add env vars:
   - JHIMS_CONSULTATION_SECRET_KEY = (set any strong random value)
   - JHIMS_CONSULTATION_PUBLIC_URL = https://consultation.jhimssoftware.com
   - JHIMS_CONSULTATION_DB = /var/data/consultation_portal.db
6. Attach persistent disk:
   - Mount path: /var/data
   - Size: 1GB or more
7. Deploy and confirm app opens on the Render URL.

Cloudflare DNS update:
- If you keep tunnel route, remove old consultation tunnel route to avoid conflict.
- Create DNS CNAME:
  consultation -> <your-render-service>.onrender.com (Proxied).

Final URL:
- https://consultation.jhimssoftware.com/consultation

Optional data migration:
- New deploy starts with a fresh DB automatically.
- If you want existing local patient/doctor data migrated, tell me and I will prepare safe migration steps.
