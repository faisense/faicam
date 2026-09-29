# Testing faicam with your cameras

**You need:** your camera's IP address, username and password. It takes about 15 minutes.

## 1. Start faicam

Open a terminal and run:

```bash
cd ~/Documents/projects/faicam
uv sync                     # first time only
uv run faicam
```

It prints:

```text
faicam is running.
Open http://localhost:7380 to add your first camera.
```

Keep this terminal open while you test. To stop faicam later, press **Ctrl+C** in it.

## 2. Add your camera

1. Open **http://localhost:7380** in your browser.
2. Click **Add your first camera** and fill in:
   - **Name:** a short name, for example `door`. Programs use it to watch the camera.
   - **IP address**, **Username** and **Password** of the camera.
   - **Brand:** choose your camera's brand, or leave **Find automatically**.
3. Click **Test connection**.

✅ You see a picture from the camera and a line like "Found a Hikvision camera · 1920×1080".

4. Click **Save**.

✅ The camera appears as a card marked **Online**, with a picture that updates every second.

**Have a stream address instead of an IP camera?** Choose **RTSP address**, paste it, and enter
the username and password if it needs them. HLS (`.m3u8`), RTMP and HTTP addresses work too.
If **Test connection** fails, the message says what the server answered, for example a wrong
password, no stream at that path, or an address that only accepts uploads (like a streaming
service's ingest address).

## 3. Watch it live

Click the camera's picture. A large live view opens, with the camera's web address under it.
Press **Esc** to close it.

That address (also on each card's **Copy link** button) works in other apps too. For example,
paste `http://localhost:7380/live/door.mjpg` into VLC (**File → Open Network**) or a browser
tab. Add `?fps=30&width=1920` to the end for full speed and size.

**Watch on your phone:** click **Settings**, choose **Phones, TVs and other computers can open
them too** under **Camera links**, and click **Save and restart**. Open the camera again: its
link now starts with this computer's network address. Open that link on your phone (on the
same Wi-Fi). ✅ The video plays. The page itself still only opens on this computer, so the
phone can watch but can't change anything.

**Pause** on a camera's card stops its streaming for everyone until you press **Resume**.
While it's paused, links say so and start playing again by themselves after Resume.

## 4. Share the camera with several programs

This is what faistream (inside faicam) is for: many programs use the camera, and the camera is
connected only once.

Open a second terminal and run:

```bash
cd ~/Documents/projects/faicam
uv run faistream view door
```

A window with the live video opens. Do the same in a third and a fourth terminal.

✅ The camera's card now says **3 programs watching**. If your camera's own web page lists
connected clients, it still shows only one: faicam.

## 5. See how it's doing

Click **Stats** at the top of the page.

✅ **Who is watching** lists the 3 programs. The **Viewers** chart goes up as you open each
window. **Camera health** shows the camera's frames per second and how long it has been
online.

If your phone is watching the camera's link (step 3), it appears under **Who is watching** too,
with your phone's IP address and app, for example "Safari on iPhone".

## 6. Test the backup camera

1. Add your second camera, as in step 2.
2. On the first camera's card, click **Edit**, set **Backup camera** to the second camera,
   and click **Save**.
3. Unplug the first camera (its network cable or its power).

✅ Within about 10 seconds the card says **On backup** and shows the second camera's picture.
The `faistream view` windows keep playing.

4. Plug the first camera back in.

✅ Within about 30 seconds the card says **Online** again, with the first camera's picture.
On **Stats**, **Time on backup** shows how long the second camera filled in.

## 7. Change other settings (optional)

Click **Settings** to ask viewers for a password on camera links, let other machines open this
page (with a password), share the cameras with programs on other machines, or change ports.
**Save and restart** applies them: faicam restarts itself and the page reconnects in a few
seconds.

Prefer no page at all? Run the cameras with faistream alone (`faistream serve`, see its README):
programs, `faistream ls` and `faistream view` work the same.

For anything the forms don't show, click **Edit all settings as YAML** in Settings. Passwords
appear as `********`; leave them as they are to keep them.

## 8. Try object detection (optional)

```bash
cd ~/Documents/projects/faivision     # the example is in faistream
uv run --with ultralytics python examples/detect.py door
```

✅ A window shows the camera with boxes around people and objects. The first run downloads
the detection model, which takes a minute.

## 9. Measure your camera (optional)

Stop faicam (**Ctrl+C** in the first terminal), then run, with your camera's stream address:

```bash
uv run faistream bench --source rtsp://admin:PASSWORD@192.168.1.21/stream1
```

✅ After about a minute it prints a table with the delay, CPU use and size of each frame for
every way of sharing it.

## 10. Leave it running (optional)

Leave `faicam` running for a day with a viewer open, and check **Stats** once in a
while. ✅ The **Memory** tile's line stays about flat, and **Reconnects** stays low.

## If something doesn't work

| What you see | What to do |
|---|---|
| "Can't reach … on port 554" | Check the IP address, and that the camera and this computer are on the same network. |
| "The camera answered, but no video stream opened" | Check the username and password. If they're right, choose the brand instead of "Find automatically", or choose **Other** and enter the stream path from the camera's manual. |
| The card stays **Offline** | faicam keeps retrying. The small grey text under the status says why it can't connect. |
| The picture is frozen or grey | Click **Edit**, open **More settings**, and set **Connect over** to **TCP**. |
| "faicam isn't answering" at the top of the page | The terminal running `faicam` was closed. Start it again. |

## For developers

```bash
uv run pytest            # all automated tests
```
