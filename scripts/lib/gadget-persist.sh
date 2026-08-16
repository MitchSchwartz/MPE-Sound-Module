#!/bin/bash
# USB gadget persist helpers — keep UAC2 bound for stable host DAW/PipeWire nodes.

mpe_gadget_persist_enabled() {
    case "${MPE_USB_GADGET_PERSIST:-1}" in
        1 | true | yes | on | TRUE | YES | ON) return 0 ;;
        *) return 1 ;;
    esac
}

# Start/bind the gadget when routing to the host OR when persist keeps the link up.
mpe_gadget_should_bind() {
    case "${MPE_AUDIO_PROFILE:-standalone}" in
        usb-host | usb-host-session) return 0 ;;
    esac
    mpe_gadget_persist_enabled
}
