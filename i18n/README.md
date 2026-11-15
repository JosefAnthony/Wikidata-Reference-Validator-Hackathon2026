## 🌍 Translations (i18n)

The interface is available in English and Kiswahili.
Users can switch language from the navbar or by adding ?uselang=sw to any URL.
The choice is saved in a cookie.

### How it works

* All interface text lives in JSON files in the i18n/ folder:
  * i18n/en.json – English (source language)
  * i18n/sw.json – Kiswahili
* i18n.py loads the files and picks the language
  (?uselang= → cookie → browser language → English).
* In HTML templates: {{ msg('wrv-key') }}
* In JavaScript: wrvMsg("wrv-key")
* If a message is missing in a language the English text is shown.
  If it is missing in English too, ⧼wrv-key⧽ is shown so it is easy to spot.

### How to add a new language (example: French)

1. Copy i18n/en.json to i18n/fr.json and translate the values (not the keys).
2. In i18n.py add the code to the list:
   
   SUPPORTED_LANGS = ["en", "sw", "fr"]
   
3. In templates/navbar.html add a link to the language menu:
   
   <li>
     <a class="dropdown-item {% if lang == 'fr' %}active{% endif %}"
        href="{{ url_for(request.endpoint, uselang='fr') }}" lang="fr">Français</a>
   </li>
   
4. Run the app and open each page with ?uselang=fr.

### Rules for translators

* Keep $1, $2 – they are replaced by numbers or names.
* Keep HTML tags like <strong>...</strong>; translate only the words inside.
* Keep emojis as they are.
* Write language names in their own language (e.g. "Kiswahili", "Français").

### Adding new text to the app

Never write English text directly in templates or JavaScript.
Add a new key to i18n/en.json (and other languages if you can), then use msg() or wrvMsg().