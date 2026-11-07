"""
Hardware test: does Python <-> Arduino work?  (Run this BEFORE bridge.py)

  python serial_test.py              auto-detect the Arduino
  python serial_test.py COM3         or give the port yourself

What it does:
  1. Lists the USB serial ports it can see.
  2. Opens the Arduino and sends two messages to the LCD, 2 seconds apart.
  3. Prints every line the Arduino sends back. Press your buttons!
     You should see BTN:PLAY and BTN:NEXT.
  Press Ctrl+C to stop.

Close the PlatformIO Serial Monitor first: only one program can use the port.
"""

import sys
import time

import serial
import serial.tools.list_ports

BAUD_RATE = 115200


def list_ports():
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("No serial ports found. Is the Arduino plugged in (data cable)?")
    for p in ports:
        print(f"  {p.device}  -  {p.description}")
    return ports


def pick_port(ports):
    for p in ports:
        info = f"{p.description} {p.manufacturer}".lower()
        if p.vid in (0x2341, 0x2A03, 0x1A86, 0x0403) or "arduino" in info or "ch340" in info:
            return p.device
    return ports[0].device if ports else None


def main():
    print("Ports found:")
    ports = list_ports()

    port = sys.argv[1] if len(sys.argv) > 1 else pick_port(ports)
    if port is None:
        sys.exit("Nothing to open.")
    print(f"\nOpening {port} at {BAUD_RATE} baud ...")

    try:
        with serial.Serial(port, BAUD_RATE, timeout=1) as ser:
            time.sleep(2)  # the Uno restarts when the port opens
            ser.write(b"LCD|Hello|From Python\n")
            print("Sent message 1 -> the LCD should show 'Hello / From Python'")

            sent_second = False
            started = time.time()
            print("Listening. Press the buttons (Ctrl+C to stop) ...")
            while True:
                if not sent_second and time.time() - started > 2:
                    ser.write(b"LCD|Second|message\n")
                    print("Sent message 2 -> the LCD should now show 'Second / message'")
                    sent_second = True
                line = ser.readline().decode("ascii", errors="ignore").strip()
                if line:
                    print("Arduino says:", line)
    except serial.SerialException as e:
        print(f"\nCould not use {port}: {e}")
        print("Tips: close the PlatformIO Serial Monitor / Arduino IDE; on Linux make sure")
        print("you are in the 'dialout' group (sudo usermod -aG dialout $USER, then log out/in).")
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
