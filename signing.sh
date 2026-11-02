#!/usr/bin/env bash
# One-time setup: create a stable, self-signed code-signing identity.
#
#   ./signing.sh
#
# Why: without an Apple Developer ID the app is ad-hoc signed, and macOS ties the
# Accessibility/Microphone grants to the binary's hash — every rebuild loses them.
# Signing with the same certificate every time gives the app a stable identity,
# so permissions survive rebuilds and updates. No Apple account needed.
#
# The certificate lives in its own keychain, protected by a password you choose
# here (never stored). Whoever can sign with it inherits the app's Microphone and
# Accessibility grants, so the keychain locks itself after 10 minutes and on sleep;
# build.sh asks for the password when it signs. It only holds this certificate.
set -e

SIGN_IDENTITY="ThomasWhisperer Self-Signed"
SIGN_KEYCHAIN="$HOME/Library/Keychains/thomaswhisperer-signing.keychain-db"

if [ -f "$SIGN_KEYCHAIN" ] && security find-certificate -c "$SIGN_IDENTITY" "$SIGN_KEYCHAIN" >/dev/null 2>&1; then
    echo "Signing identity already set up in $SIGN_KEYCHAIN"
    exit 0
fi

echo "=== Creating self-signed code-signing certificate '$SIGN_IDENTITY' ==="
read -r -s -p "Choose a password for the signing keychain: " SIGN_KEYCHAIN_PW; echo
read -r -s -p "Repeat it: " _PW2; echo
if [ -z "$SIGN_KEYCHAIN_PW" ] || [ "$SIGN_KEYCHAIN_PW" != "$_PW2" ]; then
    echo "Passwords empty or different — aborted."; exit 1
fi
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

cat > "$TMP/cert.cnf" <<EOF
[req]
distinguished_name = dn
x509_extensions    = ext
prompt             = no
[dn]
CN = $SIGN_IDENTITY
[ext]
basicConstraints = critical,CA:false
keyUsage         = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
EOF

# /usr/bin/openssl (LibreSSL) writes PKCS#12 in a format `security import` accepts.
/usr/bin/openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout "$TMP/key.pem" -out "$TMP/cert.pem" -config "$TMP/cert.cnf" 2>/dev/null
/usr/bin/openssl pkcs12 -export -inkey "$TMP/key.pem" -in "$TMP/cert.pem" \
    -name "$SIGN_IDENTITY" -out "$TMP/id.p12" -passout pass:"$SIGN_KEYCHAIN_PW"

rm -f "$SIGN_KEYCHAIN"
security create-keychain -p "$SIGN_KEYCHAIN_PW" "$SIGN_KEYCHAIN"
security set-keychain-settings -l -u -t 600 "$SIGN_KEYCHAIN"   # lock on sleep / after 10 min
security unlock-keychain -p "$SIGN_KEYCHAIN_PW" "$SIGN_KEYCHAIN"
security import "$TMP/id.p12" -k "$SIGN_KEYCHAIN" -P "$SIGN_KEYCHAIN_PW" \
    -T /usr/bin/codesign >/dev/null
# Grant Apple's signing tools access to the key without prompting.
security set-key-partition-list -S apple-tool:,apple:,codesign: -s \
    -k "$SIGN_KEYCHAIN_PW" "$SIGN_KEYCHAIN" >/dev/null

echo "  Certificate stored in $SIGN_KEYCHAIN"
echo "  Next build signs with it; grant Accessibility once more, then it sticks."
