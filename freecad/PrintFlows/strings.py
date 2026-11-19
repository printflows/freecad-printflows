# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PrintFlows. See LICENSE at the top of the add-on.
"""English and French text for the workbench. Plain Python, no FreeCAD imports."""

PRIVACY_URLS = {
    "en": "https://printflows.org/en/legal/privacy",
    # Localized slug from the site's routing table (printflow/src/i18n/routing.ts, '/legal/privacy').
    "fr": "https://printflows.org/fr/legal/confidentialite",
}

STRINGS = {
    "en": {
        "wb_tooltip": "Send a design to PrintFlows and get prices from 3D printing makers near you.",
        "cmd_name": "Print with PrintFlows",
        "cmd_tooltip": "Send the selected parts to PrintFlows and get prices from makers near you.",
        "title": "Print with PrintFlows",
        "what_label": "Send",
        "what_selected": "The selected objects",
        "what_visible": "All visible solids",
        "fmt_label": "Format",
        "fmt_3mf": "3MF (recommended)",
        "fmt_stl": "STL (binary, mm)",
        "quality_label": "Mesh quality",
        "quality_medium": "Medium",
        "quality_high": "High",
        "quality_low": "Low",
        "size_label": "Size",
        "size_value": "{x} x {y} x {z} mm",
        "size_none": "Nothing to send",
        "parts_count_one": "{count} part",
        "parts_count_other": "{count} parts",
        "privacy": "Your design is sent to PrintFlows so you can get prices from makers near you. "
        "PrintFlows keeps a copy for 24 hours so the page can load it, then deletes it. "
        "No maker sees it until you send a request. "
        'Privacy policy: <a href="{url}">{url}</a>',
        "test_base": "Test mode: this design goes to {base} instead of printflows.org, because "
        "PRINTFLOWS_BASE_URL is set. Delete that variable when the test is over.",
        "ok": "Send",
        "cancel": "Cancel",
        "no_document": "Open or create a document first, then click Print with PrintFlows again.",
        "no_selection": "Nothing is selected. Select one or more parts in the 3D view or the tree, "
        'or choose "All visible solids".',
        "no_solid": "These objects have no solid shape and are left out: {names}",
        "nothing": "There is nothing to send: none of these objects has a solid shape. "
        "PrintFlows needs closed solids, such as a Part or a PartDesign Body.",
        "too_many_triangles": "This design has {count} triangles. PrintFlows can't show a 3D preview above "
        "{limit} triangles, though makers can still price it. A lower mesh quality "
        "makes a smaller file.\n\nSend it anyway?",
        "exporting": "Exporting the design...",
        "preparing": "Getting ready to send...",
        "sending": "Sending to PrintFlows...",
        "export_failed": "FreeCAD could not export the design ({detail}). Try again, or choose the other format.",
        "busy": "A design is already being sent to PrintFlows. Wait for it to finish, or cancel it first.",
        "browser_failed": "Your design is on PrintFlows. Open this link to continue:\n\n{url}\n\n"
        "The link expires after 24 hours.",
        "unexpected": "Something went wrong while sending to PrintFlows: {detail}",
        "consent_title": "Connect to PrintFlows?",
        "consent_text": "To get prices, this add-on connects to the internet and sends your design to "
        "PrintFlows ({host}).\n\n"
        "What is sent: a mesh copy (3MF or STL) of the parts you choose, the file name, its size, "
        "this add-on's version and your language. Nothing else, and nothing is sent until you click Send.\n\n"
        "PrintFlows keeps the file for 24 hours so the page can load it, then deletes it. "
        "No maker sees it until you send a request.\n\n"
        "Allow the add-on to connect to PrintFlows?",
        "consent_privacy": 'Privacy policy: <a href="{url}">{url}</a>',
        "consent_yes": "Allow",
        "consent_no": "Don't allow",
        "consent_needed": "PrintFlows is not allowed to connect yet, so nothing was sent.",
        "withdraw_name": "Stop PrintFlows connecting",
        "withdraw_tooltip": "Withdraw your permission for this add-on to connect to PrintFlows. "
        "It asks again before the next send.",
        "withdraw_done": "PrintFlows can no longer connect from FreeCAD. Nothing is sent until you "
        "allow it again, the next time you click Send.",
    },
    "fr": {
        "wb_tooltip": "Envoyez un modèle à PrintFlows et recevez des prix de fabricants 3D près de chez vous.",
        "cmd_name": "Imprimer avec PrintFlows",
        "cmd_tooltip": "Envoyez les pièces choisies à PrintFlows et recevez des prix de fabricants près de chez vous.",
        "title": "Imprimer avec PrintFlows",
        "what_label": "Envoyer",
        "what_selected": "Les objets sélectionnés",
        "what_visible": "Tous les solides visibles",
        "fmt_label": "Format",
        "fmt_3mf": "3MF (recommandé)",
        "fmt_stl": "STL (binaire, mm)",
        "quality_label": "Qualité du maillage",
        "quality_medium": "Moyenne",
        "quality_high": "Élevée",
        "quality_low": "Basse",
        "size_label": "Taille",
        "size_value": "{x} x {y} x {z} mm",
        "size_none": "Rien à envoyer",
        "parts_count_one": "{count} pièce",
        "parts_count_other": "{count} pièces",
        "privacy": "Votre modèle est envoyé à PrintFlows pour que vous puissiez recevoir des prix de fabricants "
        "près de chez vous. PrintFlows en garde une copie pendant 24 heures pour que la page puisse la charger, "
        "puis la supprime. Aucun fabricant ne la voit avant que vous envoyiez une demande. "
        'Politique de confidentialité : <a href="{url}">{url}</a>',
        "test_base": "Mode test : ce modèle part vers {base} au lieu de printflows.org, parce que "
        "PRINTFLOWS_BASE_URL est définie. Supprimez cette variable une fois le test terminé.",
        "ok": "Envoyer",
        "cancel": "Annuler",
        "no_document": "Ouvrez ou créez d'abord un document, puis cliquez de nouveau sur Imprimer avec PrintFlows.",
        "no_selection": "Rien n'est sélectionné. Sélectionnez une ou plusieurs pièces dans la vue 3D ou "
        "l'arborescence, ou choisissez « Tous les solides visibles ».",
        "no_solid": "Ces objets n'ont pas de forme solide et sont laissés de côté : {names}",
        "nothing": "Rien à envoyer : aucun de ces objets n'a de forme solide. "
        "PrintFlows a besoin de solides fermés, comme une pièce Part ou un corps PartDesign.",
        "too_many_triangles": "Ce modèle compte {count} triangles. PrintFlows ne peut pas afficher d'aperçu 3D "
        "au-delà de {limit} triangles, mais les fabricants peuvent quand même en donner le prix. "
        "Une qualité de maillage plus basse donne un fichier plus petit.\n\nL'envoyer quand même ?",
        "exporting": "Exportation du modèle...",
        "preparing": "Préparation de l'envoi...",
        "sending": "Envoi vers PrintFlows...",
        "export_failed": "FreeCAD n'a pas pu exporter le modèle ({detail}). Réessayez ou choisissez l'autre format.",
        "busy": "Un modèle est déjà en cours d'envoi vers PrintFlows. Attendez la fin de l'envoi ou annulez-le d'abord.",
        "browser_failed": "Votre modèle est sur PrintFlows. Ouvrez ce lien pour continuer :\n\n{url}\n\n"
        "Le lien expire après 24 heures.",
        "unexpected": "Un problème est survenu pendant l'envoi vers PrintFlows : {detail}",
        "consent_title": "Se connecter à PrintFlows ?",
        "consent_text": "Pour obtenir des prix, ce module se connecte à Internet et envoie votre modèle à "
        "PrintFlows ({host}).\n\n"
        "Ce qui est envoyé : une copie maillée (3MF ou STL) des pièces que vous choisissez, le nom du fichier, "
        "sa taille, la version de ce module et votre langue. Rien d'autre, et rien n'est envoyé avant que vous "
        "cliquiez sur Envoyer.\n\n"
        "PrintFlows garde le fichier pendant 24 heures pour que la page puisse le charger, puis le supprime. "
        "Aucun fabricant ne le voit avant que vous envoyiez une demande.\n\n"
        "Autoriser le module à se connecter à PrintFlows ?",
        "consent_privacy": 'Politique de confidentialité : <a href="{url}">{url}</a>',
        "consent_yes": "Autoriser",
        "consent_no": "Ne pas autoriser",
        "consent_needed": "PrintFlows n'est pas encore autorisé à se connecter, donc rien n'a été envoyé.",
        "withdraw_name": "Empêcher PrintFlows de se connecter",
        "withdraw_tooltip": "Retirer l'autorisation de ce module de se connecter à PrintFlows. "
        "Il la redemandera avant le prochain envoi.",
        "withdraw_done": "PrintFlows ne peut plus se connecter depuis FreeCAD. Rien n'est envoyé tant que "
        "vous ne l'autorisez pas de nouveau, la prochaine fois que vous cliquerez sur Envoyer.",
    },
}


def text(key: str, locale: str = "en", **values) -> str:
    loc = "fr" if locale == "fr" else "en"
    template = STRINGS[loc].get(key) or STRINGS["en"][key]
    if key in ("privacy", "consent_privacy"):
        values.setdefault("url", PRIVACY_URLS[loc])
    return template.format(**values) if values else template


def plural(key: str, value: int, locale: str = "en", **values) -> str:
    """``key + "_one"`` or ``key + "_other"`` filled with ``count``.

    English uses the singular for 1 only; French for 0 and 1 ("1 pièce", "2 pièces").
    """
    singular = value == 1 or (locale == "fr" and value == 0)
    values.setdefault("count", value)
    return text(key + ("_one" if singular else "_other"), locale, **values)
