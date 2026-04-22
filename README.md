# 🍔 Food Voting

Εσωτερική web εφαρμογή γραφείου για συγκέντρωση παραγγελιών φαγητού. Κάθε άτομο μπαίνει στη σελίδα, διαλέγει όνομα/μαγαζί/προϊόν με επιλογές, και στο τέλος βγαίνει ομαδοποιημένη σύνοψη για τον που θα πάρει τηλέφωνο.

- **Zero dependencies** — μόνο Python stdlib (`http.server` + `sqlite3`)
- **Dark UI**, responsive, εκτύπωση/PDF της σύνοψης με ένα click
- Όλοι στο ίδιο μαγαζί (shop lock), τυχαία επιλογή για τηλέφωνο/παραλαβή, auto-close στις 6ώρες αδράνειας

## Γρήγορη εκκίνηση

```bash
python3 server.py
```

Ανοίγεις `http://localhost:3000` (ή την IP του μηχανήματος από άλλους υπολογιστές).

**Αλλαγή port / host:**

```bash
PORT=8000 HOST=0.0.0.0 python3 server.py
```

## Δομή αρχείων

| Αρχείο | Τι κάνει |
| --- | --- |
| `server.py` | HTTP server + SQLite (όλο το backend) |
| `index.html` | Single-page UI |
| `menu.json` | Ονόματα ατόμων + λίστα μαγαζιών/προϊόντων/επιλογών |
| `data.db` | SQLite DB (δημιουργείται αυτόματα, δεν μπαίνει στο git) |
| `food-voting.service` | systemd unit για production deployment |

## Επεξεργασία μενού (`menu.json`)

Το `menu.json` διαβάζεται από τον server σε κάθε request — αλλάζεις → **refresh στο browser** (χωρίς restart).

```json
{
  "people": ["ΝΙΚΟΛΑΣ", "ΑΡΗΣ", "ΜΑΡΙΟΣ", ...],
  "shops": [
    {
      "id": "gyroland",
      "name": "GYROLAND",
      "phone": "2651 092900",
      "products": [
        {
          "id": "gyro_pita_pork",
          "name": "Πίτα με γύρο χοιρινό",
          "groups": [
            {
              "id": "sauce",
              "name": "Σως",
              "type": "multi",
              "required": false,
              "options": [
                { "id": "tzatziki", "name": "Τζατζίκι" },
                { "id": "yellow", "name": "Κίτρινη σως" }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

**Τύποι group:**
- `"type": "single"` → radio (ο χρήστης επιλέγει ΜΙΑ option)
- `"type": "multi"` → checkboxes (επιλέγει ΟΣΕΣ θέλει)
- `"required": true` → υποχρεωτική συμπλήρωση πριν το submit

**Freeform shop** (σαν το «ΑΛΛΟ»):

```json
{
  "id": "other",
  "name": "ΑΛΛΟ",
  "freeform": true,
  "products": []
}
```

Ο χρήστης βλέπει textarea και γράφει ελεύθερα όλη την παραγγελία.

## Features

- **Επιλογή ονόματος**: modal με κουμπιά + πεδίο «άλλος» για guests που δεν είναι στη λίστα. Θυμάται την επιλογή ανά browser (localStorage).
- **Shop lock**: η πρώτη καταχώρηση κλειδώνει το μαγαζί. Τα υπόλοιπα γίνονται disabled μέχρι να αδειάσουν όλες οι καταχωρήσεις. Επιβάλλεται και από τον server.
- **Ποσότητα** 1-20 ανά καταχώρηση (stepper).
- **Edit / Delete** κάθε καταχώρησης από τη σύνοψη (✏️ / ✕ δίπλα στο όνομα).
- **Σύνοψη**: ομαδοποίηση ανά μαγαζί/προϊόν/επιλογές με άθροισμα τμχ. «3× Πίτα γύρος χοιρινός · Σως: Τζατζίκι → Νικόλας×2, Άρης».
- **Τηλέφωνο μαγαζιού** στη σύνοψη (tap-to-call σε κινητά).
- **Random picker**: «📞 Τυχαίος για τηλέφωνο» / «🏃 Τυχαίος για παραλαβή» από τους συμμετέχοντες.
- **🖨️ Εκτύπωση / PDF**: καθαρό μαύρο-σε-λευκό layout μόνο της σύνοψης με ημερομηνία, τηλέφωνα και ομαδοποιημένα items.
- **Auto-close**: αν δεν έρθει νέα καταχώρηση για 6 ώρες, η παραγγελία κλείνει αυτόματα (ή σβήνεται αν είναι κενή) και ανοίγει νέα. Ρύθμιση: `SESSION_TIMEOUT_SECONDS`.
- **Ιστορικό** παλιών παραγγελιών με προβολή λεπτομερειών.
- **Live auto-refresh** κάθε 4'' — όλοι βλέπουν τις καταχωρήσεις των άλλων ζωντανά.

## Περιβαλλοντικές μεταβλητές

| Μεταβλητή | Default | Περιγραφή |
| --- | --- | --- |
| `PORT` | `3000` | TCP port |
| `HOST` | `0.0.0.0` | Listen address |
| `SESSION_TIMEOUT_SECONDS` | `21600` (6h) | Μετά από πόσα sec αδράνειας κλείνει η παραγγελία |

## API (για troubleshooting)

| Method & Path | Τι κάνει |
| --- | --- |
| `GET /` | index.html |
| `GET /api/menu` | Ολόκληρο το menu.json |
| `GET /api/state` | Τρέχουσα session + orders |
| `POST /api/orders` | Νέα καταχώρηση |
| `PUT /api/orders/:id` | Επεξεργασία καταχώρησης |
| `DELETE /api/orders/:id` | Διαγραφή καταχώρησης |
| `POST /api/close` | Χειροκίνητο κλείσιμο session |
| `GET /api/history` | Λίστα κλεισμένων sessions |
| `GET /api/history/:id` | Λεπτομέρειες παλιάς session |

## Production deployment (systemd)

Στο μηχάνημα-server:

```bash
# 1) Βάλε τα αρχεία στο /home/<user>/Documents/food-voting/
#    ή κάνε git clone

# 2) Ενημέρωσε το food-voting.service με τον δικό σου user και path
#    (User=, WorkingDirectory=)

# 3) Εγκατάσταση ως service
sudo cp food-voting.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now food-voting.service

# 4) Έλεγχος
systemctl status food-voting.service
journalctl -u food-voting -f       # live logs
```

Ο server ξεκινάει αυτόματα σε κάθε boot (ακόμα και μετά από διακοπή ρεύματος) και επανεκκινείται αυτόματα αν crash-άρει.

**Restart μετά από αλλαγές στον κώδικα:**

```bash
sudo systemctl restart food-voting
```

**Αλλαγή port:** `sudo nano /etc/systemd/system/food-voting.service` → άλλαξε `Environment=PORT=8000` → `daemon-reload` + `restart`.

## Current deployment

- Host: `IQ-DJ (192.168.50.55)`
- Port: `8000`
- URL στο γραφείο: `http://192.168.50.55:8000`
