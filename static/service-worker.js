self.addEventListener("push", event => {
  let data = {};
  if (event.data) {
    try {
      data = event.data.json();
    } catch (error) {
      data = {title: "Niyoo Farm Services", body: event.data.text()};
    }
  }

  event.waitUntil(self.registration.showNotification(data.title || "Niyoo Farm Services", {
    body: data.body || "There is a new business alert.",
    icon: "/static/niyoo-icon-192.png",
    badge: "/static/niyoo-icon-192.png",
    data: {url: data.url || "/dashboard"}
  }));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const target = new URL(event.notification.data.url || "/dashboard", self.location.origin);
  if (target.origin !== self.location.origin) return;

  event.waitUntil(clients.matchAll({type: "window", includeUncontrolled: true}).then(windowClients => {
    for (const client of windowClients) {
      if (client.url.startsWith(self.location.origin) && "focus" in client) {
        client.navigate(target.href);
        return client.focus();
      }
    }
    return clients.openWindow(target.href);
  }));
});
