# Deploying behind a reverse proxy

Two hosts involved:

- **The nginx host** — wherever your reverse-proxy nginx runs, terminating TLS and fronting whatever
  subdomain you're using for this.
- **The app host** — wherever this container runs (Docker/LXC/VM, doesn't matter).

## 1. App container

On the app host:

```
git clone https://github.com/Revanito/read-later.git
cd read-later
cp .env.example .env   # fill in ONE_MIN_API_KEY + APP_PASSWORD (HARDCOVER_TOKEN optional)
docker compose up -d --build
curl -sI localhost:8086/login | head -1   # sanity check: expect HTTP/1.1 200 OK
```

(Adjust the port here and in `nginx-reverse-proxy.conf` to whatever you map in `docker-compose.yml`.)

## 2. DNS

Point `later.vaultinc.fr` at your public IP, same as any other reverse proxied service you already run
(feeds.vaultinc.fr, r6.vaultinc.fr, etc).

## 3. nginx vhost, on the nginx host

```
sudo cp nginx-reverse-proxy.conf /etc/nginx/sites-available/later
sudo ln -s /etc/nginx/sites-available/later /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Edit the copied file first to replace `<YOUR_DOMAIN>` (`later.vaultinc.fr`) and `<APP_HOST_IP>` with your
real values.

## 4. TLS

```
sudo certbot --nginx -d later.vaultinc.fr
```

This rewrites the vhost in place to add the 443/TLS server block and an http→https redirect. Verify with
`sudo nginx -t && sudo systemctl reload nginx` (certbot usually reloads for you).

## 5. Verify

```
curl -I https://later.vaultinc.fr
```

Should return a `303 See Other` redirect to `/login` from FastAPI (the app is password-protected), not nginx's default page or a 502 (502 means nginx can't reach the
app host on the expected port — check the container is up and no firewall rule is blocking it).
