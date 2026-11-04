#include <Arduino.h>
#include <LiquidCrystal_I2C.h>
#include <Wire.h>

LiquidCrystal_I2C lcd(0x27, 16, 2);

#define btn_play 3
#define btn_next 2
#define LCD_COLS 16 // 16 columns

// button state/memory
int lastPlayState = HIGH;
int lastNextState = HIGH;

String incoming = "";

void showLine(int row, String text)
{
  // 1. Too long? Cut it down to 16 characters.
  if (text.length() > LCD_COLS)
  {
    text = text.substring(0, LCD_COLS);
  }

  // 2. Too short? Add spaces at the end until it is 16 characters.
  while (text.length() < LCD_COLS)
  {
    text += ' ';
  }

  lcd.setCursor(0, row);
  lcd.print(text);
}

void handleMessage(String msg)
{
  Serial.println("GOT: " + msg);
  // Ignore anything that doesn't start with "LCD|"
  if (!msg.startsWith("LCD|"))
  {
    return;
  }

  // LCD|Hello|World
  // Find the two '|' separators.
  int firstBar = msg.indexOf('|');                // first "|"
  int secondBar = msg.indexOf('|', firstBar + 1); // second "|"

  // indexOf gives -1 when nothing is found, so the message was incomplete.
  if (secondBar == -1)
  {
    return;
  }

  // Cut out the two pieces.
  //   substring(a, b)  = from position a up to (not including) position b
  //   substring(a)     = from position a to the end
  String line1 = msg.substring(firstBar + 1, secondBar);
  String line2 = msg.substring(secondBar + 1);

  showLine(0, line1);
  showLine(1, line2);
}

// this function's purpose is to make button pressed once, avoids holding
bool wasPressed(int pin, int &lastState)
{                                                   // parameters = (btn_pin, btn_state)
  int now = digitalRead(pin);                       // for pushbutton pins parameter
  bool pressed = (lastState == HIGH && now == LOW); // lastState = not pressed, now = pressed

  if (now != lastState)
  {
    delay(20); // bounce?
  }

  lastState = now; // will be pressed
  return pressed;  // return pressed value true/false
}

// Reads whatever characters have arrived over USB.
// A message is finished when we see the newline character '\n'.
void readSerial()
{
  while (Serial.available() > 0) // while there are unread characters waiting
  {
    char c = Serial.read(); // take ONE character

    if (c == '\n') // end of message!
    {
      handleMessage(incoming); // process it
      incoming = "";           // start fresh for the next message
    }
    else if (c != '\r') // ignore '\r' (some programs send "\r\n")
    {
      if (incoming.length() < 60) // safety limit: the Uno has very little memory
      {
        incoming += c; // add the character to the message
      }
    }
  }
}

void setup()
{
  Serial.begin(115200);
  lcd.init();
  lcd.backlight();
  pinMode(btn_play, INPUT_PULLUP);
  pinMode(btn_next, INPUT_PULLUP);

  showLine(0, "Lyrics LCD");
  showLine(1, "Ready");
}

void loop()
{
  readSerial(); // 1. anything arrived from the computer?

  if (wasPressed(btn_play, lastPlayState))
  {
    Serial.println("BTN:PLAY");
  }

  if (wasPressed(btn_next, lastNextState))
  {
    Serial.println("BTN:NEXT");
  }
}
// command
// pio device monitor - b 115200 --echo = test input lcd