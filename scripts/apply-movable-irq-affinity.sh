#!/bin/bash
# Move movable IRQs off CPU0 — leave xhci (30) and arch_timer on the IRQ cores.
#
# GICv2 targets the lowest core in each IRQ mask; with irqaffinity=0,1 that is
# always CPU0. xhci (USB audio) cannot move (empty effective_affinity), so every
# other interrupt must leave CPU0 or it shares the core with the transport.
#
# Idempotent. Called by mpe-irq-affinity.service at boot and after jackd restarts
# are not in progress.

set -euo pipefail

MOVABLE_IRQS=(41 42 43 28 57)
TARGET_CPU=1

for irq in "${MOVABLE_IRQS[@]}"; do
    aff="/proc/irq/${irq}/smp_affinity_list"
    if [ ! -w "$aff" ]; then
        echo "apply-movable-irq-affinity: skip IRQ ${irq} (not writable)" >&2
        continue
    fi
    echo "$TARGET_CPU" >"$aff"
    got="$(cat "$aff" 2>/dev/null || echo '?')"
    echo "apply-movable-irq-affinity: IRQ ${irq} -> ${got}"
done

exit 0
