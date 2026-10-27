"""Pure nginx/unit configuration generation for the Pi music login boundary."""
import ipaddress
import re


def hosts(hostname, addresses):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}', hostname):
        raise ValueError('Invalid music hostname')
    result = [hostname.lower(), 'localhost', '127.0.0.1']
    for value in addresses:
        address = ipaddress.ip_address(value)
        if address.version == 4 and address.is_private and not address.is_link_local and not address.is_unspecified:
            result.append(str(address))
    return list(dict.fromkeys(result))


def origins(names):
    return [f'https://{host}{port}' for host in names for port in ('', ':443', ':8443')]


def nginx_config(hostname, addresses):
    names = hosts(hostname, addresses)
    allowed = ' '.join('"' + host + '" 1;' for host in names)
    origin_map = ' '.join('"' + origin + '" 1;' for origin in origins(names))
    acl = '''allow 127.0.0.1;
 allow 192.168.0.0/16;
 allow 10.0.0.0/8;
 allow 172.16.0.0/12;
 deny all;'''
    return f'''user www-data;
worker_processes 1;
pid /run/pi-audio-web/nginx.pid;
error_log stderr warn;
events {{ worker_connections 128; }}
http {{
 include /etc/nginx/mime.types;
 server_tokens off;
 access_log off;
 client_body_temp_path /run/pi-audio-web/body;
 proxy_temp_path /run/pi-audio-web/proxy;
 fastcgi_temp_path /run/pi-audio-web/fastcgi;
 uwsgi_temp_path /run/pi-audio-web/uwsgi;
 scgi_temp_path /run/pi-audio-web/scgi;
 upstream music_login {{ server unix:/run/pi-audio-login/auth.sock; }}
 limit_req_zone $binary_remote_addr zone=music_login:1m rate=1r/s;
 map $host $music_host_ok {{ default 0; {allowed} }}
 map $http_origin $music_origin_ok {{ default 0; "" 1; {origin_map} }}
 map $http_accept $music_html {{ default 0; ~*text/html 1; }}
 map $http_upgrade $connection_upgrade {{ default upgrade; '' close; }}
 server {{
  listen 80;
  server_name {hostname};
  {acl}
  if ($music_host_ok = 0) {{ return 400; }}
  location / {{
   if ($request_method !~ ^(GET|HEAD)$) {{ return 405; }}
   # Access rules run before this redirect (do not return at server scope).
   try_files /nonexistent-pi-audio-redirect @https;
  }}
  location @https {{ return 308 https://$host$request_uri; }}
 }}
 server {{
  listen 443 ssl;
  listen 8443 ssl;
  server_name {hostname};
  ssl_certificate /etc/pi-audio-tray/music/tls/server.pem;
  ssl_certificate_key /etc/pi-audio-tray/music/tls/server.key;
  ssl_protocols TLSv1.2 TLSv1.3;
  ssl_session_tickets off;
  {acl}
  if ($music_host_ok = 0) {{ return 400; }}
  if ($music_origin_ok = 0) {{ return 403; }}
  client_max_body_size 1m;
  add_header X-Content-Type-Options nosniff always;
  add_header Strict-Transport-Security "max-age=86400" always;
  location = /sw.js {{
   alias /usr/local/share/pi-audio-tray/session-worker.js;
   add_header Cache-Control "no-store";
   default_type application/javascript;
  }}
  location = /_session_check {{
   internal;
   proxy_pass http://music_login/check;
   proxy_pass_request_body off;
   proxy_set_header Content-Length "";
   proxy_set_header Authorization "";
   proxy_set_header Cookie $http_cookie;
  }}
  location ~ ^/(login|logout|account)$ {{
   limit_req zone=music_login burst=6 nodelay;
   limit_req_status 429;
   client_max_body_size 4k;
   proxy_pass http://music_login;
   proxy_set_header Authorization "";
   proxy_set_header X-Real-IP $remote_addr;
   proxy_set_header Cookie $http_cookie;
  }}
  location ~ ^/(script|script-api|api/script)(/|$) {{ return 403; }}
  location @need_login {{
   if ($music_html = 1) {{ return 302 /login; }}
   return 401;
  }}
  location / {{
   auth_request /_session_check;
   error_page 401 = @need_login;
   proxy_hide_header Cache-Control;
   add_header Cache-Control "private, no-store" always;
   add_header X-Content-Type-Options nosniff always;
   add_header Strict-Transport-Security "max-age=86400" always;
   proxy_pass http://127.0.0.1:8080;
   proxy_http_version 1.1;
   proxy_set_header Host $http_host;
   proxy_set_header Authorization "";
   proxy_set_header Cookie "";
   proxy_set_header Upgrade $http_upgrade;
   proxy_set_header Connection $connection_upgrade;
   proxy_set_header X-Forwarded-Proto https;
   proxy_set_header X-Forwarded-For $remote_addr;
   proxy_read_timeout 300s;
  }}
 }}
}}
'''


LOGIN_UNIT = '''[Unit]
Description=Pi Audio persistent browser login
Before=pi-audio-web.service
[Service]
Type=notify
TimeoutStartSec=10
User=pi-audio-auth
Group=www-data
ExecStart=/usr/bin/python3 -I /usr/local/lib/pi-audio-tray/web-login.py
StateDirectory=pi-audio-login
StateDirectoryMode=0700
RuntimeDirectory=pi-audio-login
RuntimeDirectoryMode=0750
UMask=0077
NoNewPrivileges=yes
CapabilityBoundingSet=
ProtectHome=yes
ProtectSystem=strict
PrivateTmp=yes
PrivateDevices=yes
RestrictAddressFamilies=AF_UNIX
RestrictSUIDSGID=yes
LimitCORE=0
Restart=on-failure
RestartSec=3
[Install]
WantedBy=multi-user.target
'''

WEB_DEPENDENCY = '''[Unit]
Requires=pi-audio-login.service
After=pi-audio-login.service
'''

# A network-only worker prevents cached player pages hiding an expired login.
# Only myMPD's caches are removed; unrelated applications on this host are left.
SESSION_WORKER = '''self.addEventListener('install', event => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil((async () => {
  for (const name of await caches.keys()) {
    if (name.startsWith('myMPD-cache-')) await caches.delete(name);
  }
  await self.clients.claim();
})()));
'''
