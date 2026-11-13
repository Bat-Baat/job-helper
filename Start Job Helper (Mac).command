#!/bin/bash
cd "$(dirname "$0")"
echo ""
echo "  Starting Job Helper... (the first start takes a few minutes)"
echo "  Keep this window open while you use Job Helper. Close it when you're done."
echo ""
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "  Installing helper tool (one time only)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
if ! uv run --python 3.12 python -m jobbot.firstrun || \
   ! uv run --python 3.12 streamlit run app.py --server.headless false --browser.gatherUsageStats false --theme.base light; then
  echo ""
  echo "  Something went wrong. Take a screenshot of this window and send it to whoever gave you Job Helper."
  read -n 1 -s -r -p "  Press any key to close."
fi
