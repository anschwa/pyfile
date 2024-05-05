"""app.py

TODOs:
- Do not overwrite files with the same name
- Add tests to checksum uploaded files
- Better/custom text filenames

"""

import base64
import http
import http.server
import mimetypes
import os
import re
import secrets
import shutil
import sys
import tempfile
import urllib.parse

PORT = 8080
FILEDIR = "tmp"


def render_homepage_html(file_data=None):
    """render_homepage_html renders templates HTML for the app"""
    uploaded_files_html = ""
    for fd in file_data:
        name, size = fd["filename"], human_bytes(fd["filesize"])
        row = f'<tr><td><a href="/upload/{name}">{name}</a></td><td>{size}</td></tr>'
        uploaded_files_html += row

    return f"""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>PyFile</title>
    <style>
    * {{ margin 0; padding: 0; }}
    body {{ margin: 10px auto; max-width: 80ch; font-family: sans-serif; font-size: 100%; }}
    main {{ margin: 0 20px; }}
    h1 {{ font-size: 26px; }}
    form {{ display: flex; flex-direction: column; gap: 10px; }}
    fieldset {{ display: flex; justify-content: space-between; border: none;}}
    textarea {{ padding: 10px; min-height: 80px; resize: vertical; text-wrap: nowrap; font-size: 16px; font-family: monospace; }}
    input[name="submit"] {{ margin: 0 auto; padding: 0 10px; }}
    table {{ margin: 20px 0; padding: 10px 0; border-top: 1px solid #888; width: 100%; border-spacing: 0 6px; font-family: monospace; font-size: 16px; }}
    table th:nth-child(1), td:nth-child(1) {{ text-align: left; }}
    table th:nth-child(2), td:nth-child(2) {{ text-align: right; }}
    tbody tr:hover {{ background-color: #f9f9f9; }}
    </style>
  </head>

  <body>
    <main>
      <h1>Upload files or text</h1>
      <form action="/upload" method="post" enctype="multipart/form-data">
        <fieldset>
          <input style="flex-grow: 1;" name="files" type="file" multiple />
          <input name="submit" type="submit" value="Upload" />
        </fieldset>
        <textarea name="text" placeholder="Lorem ipsum…"></textarea>
      </form>

      <table>
        <thead><tr><th>File</th><th>Size</th></tr></thead>
        <tbody>{uploaded_files_html}</tbody>
      </table>
    </main>
  </body>
</html>
""".encode(
        "utf-8"
    )


def human_bytes(num):
    """human_bytes rounds bytes to the nearest human friendly unit"""
    for unit in ("B", "K", "M", "G"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}"
        num /= 1024.0
    return "?"


def get_uploaded_file_data(path=FILEDIR):
    """get_uploaded_file_data returns a list of file uploads"""

    file_data = []
    with os.scandir(path) as uploads:
        for entry in uploads:
            if not entry.name.startswith(".") and entry.is_file():
                file_data.append(
                    {
                        "path": entry.path,
                        "filename": entry.name,
                        "filesize": entry.stat().st_size,
                    }
                )

    return file_data


def rand_b32_str(n=5):
    """rand_b32_str returns a random string of base32 encoded bytes"""
    b = base64.b32encode(secrets.token_bytes(n))
    return b.decode("utf-8").lower()


def get_header_opt(key, header):
    """get_header_opt returns the value from a quoted or unquoted HTTP
    header piece such as key="value" or key=value"""

    p = re.compile(rf'{key}=([^\s";]+)|{key}="([^"]+)"')
    m = p.search(header)
    if m is None:
        return ""

    a, b = m.groups()
    if a:
        return a
    return b.strip()


def parse_multipart(req_headers, req_body):
    """parse_multipart parses a multipart/form-data request body and
    returns a dict containing each upload file"""

    content_type = req_headers["Content-Type"]
    boundary = get_header_opt("boundary", content_type)
    separator = f"--{boundary}\r\n".encode("utf-8")
    terminator = f"--{boundary}--\r\n".encode("utf-8")

    multipart_form_data = []

    # Look for the first file
    while True:
        if req_body.readline() == separator:
            break

    # Read multipart data
    while True:
        line = req_body.readline()
        if not line.startswith(b"Content-Disposition:"):
            continue

        header = line.rstrip().decode("utf-8")
        form_name = get_header_opt("name", header)
        filename = get_header_opt("filename", header)

        # Use temp file as a bytes buffer
        buf = tempfile.TemporaryFile(mode="w+b")

        # Read until end of headers
        while True:
            line = req_body.readline()
            if line == b"\r\n":
                break

        # Read file content into buffer
        while True:
            line = req_body.readline()
            if line in (separator, terminator):
                break
            buf.write(line)

        multipart_form_data.append(
            {
                "form_name": form_name,
                "filename": filename,
                "buf": buf,
            }
        )

        # Check if done
        if line == terminator:
            # Remove trailing CRLF from HTTP
            buf.seek(-2, 2)
            buf.truncate()
            break

    # Done
    return multipart_form_data


class AppHandler(http.server.BaseHTTPRequestHandler):
    """AppHandler handles all web requests"""

    def do_GET(self):
        """do_GET handles GET requests"""
        if self.path == "/":
            self.send_homepage()
            return

        if not self.path.startswith("/upload/"):
            self.send_error(http.HTTPStatus.NOT_FOUND)
            return

        url = urllib.parse.urlparse(self.path)
        _, basename = urllib.parse.unquote(url.path).split("/upload/")

        upload = None
        for ufd in get_uploaded_file_data():
            if basename == ufd["filename"]:
                upload = ufd
                break

        if upload is None:
            self.send_error(http.HTTPStatus.NOT_FOUND)
            return

        self.send_filedata(upload)

    def do_POST(self):
        """do_POST handles POST requests"""
        if self.path != "/upload":
            self.send_error(http.HTTPStatus.NOT_IMPLEMENTED)
            return

        multipart_form_data = parse_multipart(self.headers, self.rfile)

        for part in multipart_form_data:
            filename, buf = part["filename"], part["buf"]
            match part["form_name"]:
                case "files":
                    if filename == "":
                        continue
                case "text":
                    # Skip empty form
                    if buf.tell() == 0:
                        continue
                    filename = f"text_{rand_b32_str()}.txt"
                case _:
                    continue

            # Save uploaded files
            with open(f"{FILEDIR}/{filename}", "wb") as f:
                buf.seek(0)
                f.write(buf.read())
            buf.close()

        # Done
        self.send_redirect("/")

    def send_homepage(self):
        """send_homepage sends the homepage to the client"""
        file_data = get_uploaded_file_data()
        self.send_bytes(render_homepage_html(file_data), "text/html")

    def send_filedata(self, filedata):
        """send_filedata sends filedata to the client"""
        ctype, _ = mimetypes.guess_type(filedata["filename"])
        if not ctype:
            ctype = "application/octet-stream"

        self.send_response(http.HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", filedata["filesize"])
        self.end_headers()

        with open(filedata["path"], "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def send_bytes(self, body, mime="text/plain"):
        """send_bytes sends bytes to the client"""
        self.send_response(http.HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_redirect(self, loc):
        """send_redirect sends a redirect to the client"""
        self.send_response(http.HTTPStatus.FOUND)
        self.send_header("Location", loc)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", 0)
        self.end_headers()


if __name__ == "__main__":
    os.makedirs(FILEDIR, exist_ok=True)

    httpd = http.server.ThreadingHTTPServer(("", PORT), AppHandler)
    host, port = httpd.socket.getsockname()[:2]

    info = f"Serving at http://{host}:{port}/ ..."
    print(info)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nExiting ...")
        sys.exit(0)
