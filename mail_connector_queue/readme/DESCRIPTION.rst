This module uses the Connector Queue to send any emails generated by Odoo,
by creating a queue job for each one. This allows the user to define different
priorities for these emails, enabling, for instance, running high volume mass
mailing with low priority, to avoid blocking regular notifications.
