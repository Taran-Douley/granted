#!/usr/bin/env bash
# Install Granted for this folder, on a Mac or a Linux computer. Run it once on
# each computer that will use the folder:
#
#     ./install.sh
#
# The program and its libraries go in your user data folder, not in here, so a
# folder synced through OneDrive or Dropbox stays small. Your key goes in your
# user config folder, never in here: a shared folder is the wrong place for it.
#
# Unattended (for IT, or for testing): set GRANTED_INSTALL_YES=1 and
# GRANTED_INSTALL_NAME, plus ANTHROPIC_API_KEY, or AWS_ACCESS_KEY_ID and
# AWS_SECRET_ACCESS_KEY. GRANTED_INSTALL_ARCHIVE and GRANTED_INSTALL_SCHEDULE=1
# are optional.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/.granted/app"
if [[ "$(uname)" == "Darwin" ]]; then
  DATA="$HOME/Library/Application Support/Granted"
  CONFIG="$DATA"
else
  DATA="${XDG_DATA_HOME:-$HOME/.local/share}/granted"
  CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/granted"
fi
VENV="${GRANTED_VENV:-$DATA/venv}"
CREDS="${GRANTED_CREDENTIALS:-$CONFIG/credentials.env}"
YES="${GRANTED_INSTALL_YES:-}"

say() { printf '\n  %s\n' "$1"; }
ask() {  # ask "question" "default"  ->  $REPLY
  if [[ -n "$YES" ]]; then REPLY="$2"; return; fi
  read -r -p "  $1 " REPLY || true
  REPLY="${REPLY:-$2}"
}

printf '\n  Granted: setting up %s\n' "$HERE"

# 1. Python 3.11 or newer --------------------------------------------------
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 &&
     "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [[ -z "$PY" ]]; then
  say "Granted needs Python 3.11 or newer, and this computer does not have it."
  if [[ "$(uname)" == "Darwin" ]]; then
    say "Install it from https://www.python.org/downloads/ and run ./install.sh again."
  else
    say "Install it (for example: sudo apt install python3 python3-venv), then run ./install.sh again."
  fi
  exit 1
fi

# 2. How Granted reaches the AI model -------------------------------------
PROVIDER=""
if [[ -f "$CREDS" ]]; then
  ask "This computer already has a key saved for Granted. Keep it? [Y/n]" "y"
  [[ "$REPLY" =~ ^[Nn] ]] || PROVIDER="keep"
fi
if [[ -z "$PROVIDER" ]]; then
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    PROVIDER="anthropic"
  elif [[ -n "${AWS_ACCESS_KEY_ID:-}" && -n "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
    PROVIDER="bedrock"
  elif [[ -n "$YES" ]]; then
    say "GRANTED_INSTALL_YES needs ANTHROPIC_API_KEY, or AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
    exit 1
  else
    say "Granted uses an AI model to read funding calls and draft answers. How should it reach one?"
    printf '    1  An Anthropic API key (simplest: console.anthropic.com)\n'
    printf '    2  An AWS account with Amazon Bedrock\n'
    ask "Choose 1 or 2 [1]:" "1"
    if [[ "$REPLY" == "2" ]]; then PROVIDER="bedrock"; else PROVIDER="anthropic"; fi
  fi
fi

# 3. The program, in its own environment ----------------------------------
say "Installing the program (a minute or two the first time)..."
mkdir -p "$(dirname "$VENV")"
if [[ ! -x "$VENV/bin/python" ]]; then
  if ! "$PY" -m venv "$VENV"; then
    say "Python cannot create environments here. On Debian or Ubuntu: sudo apt install python3-venv"
    exit 1
  fi
fi
EXTRA=""
if [[ "$PROVIDER" == "anthropic" ]] ||
   { [[ "$PROVIDER" == "keep" ]] && grep -q '^GRANTED_PROVIDER=anthropic' "$CREDS"; }; then
  EXTRA="[anthropic]"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
if ! "$VENV/bin/python" -m pip install --quiet "$APP$EXTRA"; then
  say "The install did not finish. Check the internet connection and run ./install.sh again."
  exit 1
fi

write_creds() {
  mkdir -p "$(dirname "$CREDS")"
  ( umask 077; printf '%s\n' "$@" > "$CREDS" )
  chmod 600 "$CREDS"
}
case "$PROVIDER" in
  anthropic)
    KEY="${ANTHROPIC_API_KEY:-}"
    if [[ -z "$KEY" ]]; then
      read -r -s -p "  Paste your Anthropic API key (it will not show): " KEY; echo
    fi
    write_creds "# Granted: how this computer reaches the model. Keep this file private." \
                "GRANTED_PROVIDER=anthropic" "ANTHROPIC_API_KEY=$KEY"
    ;;
  bedrock)
    AK="${AWS_ACCESS_KEY_ID:-}"; SK="${AWS_SECRET_ACCESS_KEY:-}"; RG="${AWS_REGION:-us-west-2}"
    if [[ -z "$AK" || -z "$SK" ]]; then
      read -r -p "  AWS access key id: " AK
      read -r -s -p "  AWS secret access key (it will not show): " SK; echo
      ask "AWS region [us-west-2]:" "us-west-2"; RG="$REPLY"
    fi
    write_creds "# Granted: how this computer reaches the model. Keep this file private." \
                "GRANTED_PROVIDER=bedrock" "AWS_ACCESS_KEY_ID=$AK" \
                "AWS_SECRET_ACCESS_KEY=$SK" "AWS_REGION=$RG"
    ;;
esac

# 4. The organisation, and where its past applications are ----------------
if [[ ! -f "$HERE/granted.json" ]]; then
  NAME="${GRANTED_INSTALL_NAME:-}"
  if [[ -z "$NAME" ]]; then ask "Your organisation's name:" ""; NAME="$REPLY"; fi
  if [[ -z "$NAME" ]]; then say "An organisation name is needed."; exit 1; fi
  ARCHIVE="${GRANTED_INSTALL_ARCHIVE:-}"
  if [[ -z "$ARCHIVE" && -z "$YES" ]]; then
    say "Where are your past funding applications? Press Enter to use the 'Past applications' folder in here, or drag another folder into this window."
    ask "Folder:" ""
    # A dragged-in folder arrives quoted, or with its spaces escaped.
    ARCHIVE="$(printf '%s' "$REPLY" | sed -e "s/^[\"']//" -e "s/[\"'] *\$//" -e 's/\\ / /g' -e 's/ *$//')"
  fi
  SETUP=(setup --home "$HERE" --name "$NAME")
  if [[ -n "$ARCHIVE" ]]; then SETUP+=(--archive "$ARCHIVE"); fi
  "$VENV/bin/granted" "${SETUP[@]}"
else
  say "This folder is already set up, so its settings and ORG.md were left as they are."
fi

# 5. A way to run it, and a daily schedule --------------------------------
RUN="$HERE/run-granted.sh"
cat > "$RUN" <<EOF
#!/usr/bin/env bash
# Run Granted for this folder. The last run's output is kept in .granted/last-run.log.
# pipefail: without it, tee's success would hide a failed run from cron.
set -o pipefail
cd "\$(dirname "\$0")"
"$VENV/bin/granted" run --home "\$(pwd)" "\$@" 2>&1 | tee .granted/last-run.log
EOF
chmod +x "$RUN"

ask "Run Granted automatically every morning at 8? [y/N]" "${GRANTED_INSTALL_SCHEDULE:+y}"
if [[ "$REPLY" =~ ^[Yy] ]]; then
  LINE="0 8 * * * \"$RUN\" >/dev/null 2>&1"
  { crontab -l 2>/dev/null | grep -vF "$RUN" || true; echo "$LINE"; } | crontab -
  say "Scheduled: every day at 08:00."
else
  say "Not scheduled. To run it yourself: $RUN"
fi

say "Done. Open ORG.md and fill the gaps it lists, then run: $RUN"
say "Afterwards, open Dashboard.html to see what it found."
echo
