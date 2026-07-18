#!/bin/bash
# Ensure I2C modules load early

echo "Setting up early I2C module loading..."

# Add to /etc/modules if not already present
if ! grep -q "i2c-dev" /etc/modules; then
    echo "i2c-dev" | sudo tee -a /etc/modules
    echo "✓ Added i2c-dev to /etc/modules"
else
    echo "✓ i2c-dev already in /etc/modules"
fi

if ! grep -q "i2c-bcm2835" /etc/modules; then
    echo "i2c-bcm2835" | sudo tee -a /etc/modules
    echo "✓ Added i2c-bcm2835 to /etc/modules"
else
    echo "✓ i2c-bcm2835 already in /etc/modules"
fi

# Verify /boot/config.txt has I2C enabled
echo ""
echo "Checking I2C configuration in /boot/config.txt..."
if grep -q "dtparam=i2c_arm=on" /boot/firmware/config.txt 2>/dev/null || \
   grep -q "dtparam=i2c_arm=on" /boot/config.txt 2>/dev/null; then
    echo "✓ I2C is enabled in /boot/config.txt"
else
    echo "WARNING: I2C may not be enabled in /boot/config.txt"
    echo "Please add the following lines to /boot/config.txt:"
    echo "  dtparam=i2c_arm=on"
    echo "  dtparam=i2c1=on"
    echo "  dtparam=i2c_arm_baudrate=400000"
fi

echo ""
echo "✓ I2C module loading configured"
echo "⚠ Reboot required for changes to take effect"
