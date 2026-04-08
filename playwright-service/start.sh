#!/bin/bash
# Startup script for Playwright service with Xvfb support
# This provides a virtual display for headed browser mode (required for Xero login)

set -e  # Exit immediately on any error

# Start Xvfb (X Virtual Framebuffer) in the background
# This creates a virtual display that allows headed browsers to run without a physical monitor
# Screen size is slightly larger than viewport (1920x1200) to provide headroom for window decorations
echo "Starting Xvfb virtual display..."
Xvfb :99 -screen 0 1920x1200x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!

# Wait for Xvfb to be ready (up to 15 seconds)
echo "Waiting for Xvfb to be ready..."
XVFB_READY=false
for i in $(seq 1 15); do
    if xdpyinfo -display :99 > /dev/null 2>&1; then
        echo "Xvfb is ready on display :99"
        XVFB_READY=true
        break
    fi
    echo "Waiting for Xvfb... ($i/15)"
    sleep 1
done

# FAIL HARD if Xvfb didn't start - don't continue with a broken display
if [ "$XVFB_READY" = false ]; then
    echo "ERROR: Xvfb failed to start after 15 seconds. Exiting."
    kill $XVFB_PID 2>/dev/null || true
    exit 1
fi

# Set the DISPLAY environment variable to use the virtual display
export DISPLAY=:99

# Final verification - check process is still running
if ! kill -0 $XVFB_PID 2>/dev/null; then
    echo "ERROR: Xvfb process died after startup. Exiting."
    exit 1
fi

echo "Xvfb started successfully on display :99 (PID: $XVFB_PID)"

# Start the FastAPI application
echo "Starting Playwright service..."
exec python run.py
