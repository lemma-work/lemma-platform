#!/bin/sh
# Generate runtime-config.js with environment variables
# This allows NEXT_PUBLIC_* vars to be set at runtime, not build time

# Generate the runtime config JS file in public directory (client-side).
# Keep this generic so Docker env additions do not require frontend code changes.
mkdir -p /app/public
node > /app/public/runtime-config.js <<'NODE'
const env = {};

for (const [key, value] of Object.entries(process.env)) {
  if (key.startsWith("NEXT_PUBLIC_")) {
    env[key] = value ?? "";
  }
}

env.NEXT_PUBLIC_API_URL ||= "https://api.localhost";
env.NEXT_PUBLIC_SITE_URL ||= "http://localhost:3000";
env.NEXT_PUBLIC_AUTH_URL ||= env.NEXT_PUBLIC_SITE_URL;
env.NEXT_PUBLIC_SESSION_TOKEN_DOMAIN ||= "";

process.stdout.write(`window.__ENV = ${JSON.stringify(env, null, 2)};\n`);
NODE

# Create .well-known directory if it doesn't exist
mkdir -p /app/public/.well-known

# Generate Microsoft identity verification JSON file
# MICROSOFT_APPLICATION_IDS is comma-separated list of Azure AD app IDs
cat > /app/public/.well-known/microsoft-identity-association.json <<EOF
{
  "associatedApplications": [
$(echo "${MICROSOFT_APPLICATION_IDS:-}" | tr ',' '\n' | while read -r appId; do
  appId=$(echo "$appId" | tr -d ' ')
  if [ -n "$appId" ]; then
    echo "    {"
    echo "      \"applicationId\": \"$appId\""
    echo "    },"
  fi
done | sed '$ s/,$//')
  ]
}
EOF

# Also copy to standalone output directories if they exist.
for standalone_dir in /app /app/app; do
  if [ -d "$standalone_dir" ]; then
    mkdir -p "$standalone_dir/public"
    cp /app/public/runtime-config.js "$standalone_dir/public/runtime-config.js" 2>/dev/null || true
    cp -r /app/public/.well-known "$standalone_dir/public/" 2>/dev/null || true
    cp /app/public/runtime-config.js "$standalone_dir/.next/runtime-config.js" 2>/dev/null || true
    cp -r /app/public/.well-known "$standalone_dir/.next/" 2>/dev/null || true
  fi
done

echo "Client runtime config generated:"
cat /app/public/runtime-config.js

echo "Microsoft identity config generated:"
cat /app/public/.well-known/microsoft-identity-association.json

# Start the Next.js server. Monorepo root builds emit app/server.js.
if [ -f /app/server.js ]; then
  exec node /app/server.js
fi

exec node /app/app/server.js
