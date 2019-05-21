# -*- coding: utf-8 -*-
# Copyright 2017 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import mock
from openerp import api
from openerp.tests import common
from openerp.modules.registry import RegistryManager
from openerp.addons.connector.exception import RetryableJobError
from openerp.addons.connector.connector import pg_try_advisory_lock
from openerp.addons.component.core import WorkContext
from openerp.addons.component.tests.common import TransactionComponentCase


class TestAdvisoryLock(TransactionComponentCase):

    def setUp(self):
        super(TestAdvisoryLock, self).setUp()
        self.registry2 = RegistryManager.get(common.get_db_name())
        self.cr2 = self.registry2.cursor()
        self.env2 = api.Environment(self.cr2, self.env.uid, {})

        @self.addCleanup
        def reset_cr2():
            # rollback and close the cursor, and reset the environments
            self.env2.reset()
            self.cr2.rollback()
            self.cr2.close()

    def test_concurrent_lock(self):
        """ 2 concurrent transactions cannot acquire the same lock """
        # the lock is based on a string, a second transaction trying
        # to acquire the same lock won't be able to acquire it
        lock = 'import_record({}, {}, {}, {})'.format(
            'backend.name',
            1,
            'res.partner',
            '999999',
        )
        acquired = pg_try_advisory_lock(self.env, lock)
        self.assertTrue(acquired)
        # we test the base function
        inner_acquired = pg_try_advisory_lock(self.env2, lock)
        self.assertFalse(inner_acquired)

    def test_concurrent_import_lock(self):
        """ A 2nd concurrent transaction must retry """
        # the lock is based on a string, a second transaction trying
        # to acquire the same lock won't be able to acquire it
        lock = 'import_record({}, {}, {}, {})'.format(
            'backend.name',
            1,
            'res.partner',
            '999999',
        )

        backend = mock.MagicMock()
        backend.env = self.env
        work = WorkContext(model_name='res.partner',
                           collection=backend)
        # we test the function through a Component instance
        component = work.component_by_name('base.connector')
        # acquire the lock
        component.advisory_lock_or_retry(lock)

        # instanciate another component using a different odoo env
        # hence another PG transaction
        backend2 = mock.MagicMock()
        backend2.env = self.env2
        work2 = WorkContext(model_name='res.partner',
                            collection=backend2)
        component2 = work2.component_by_name('base.connector')
        with self.assertRaises(RetryableJobError) as cm:
            component2.advisory_lock_or_retry(lock, retry_seconds=3)
            self.assertEquals(cm.exception.seconds, 3)
