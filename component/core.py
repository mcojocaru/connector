# -*- coding: utf-8 -*-
# Copyright 2017 Camptocamp SA
# Copyright 2017 Odoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

from collections import defaultdict, OrderedDict

from odoo import models
from odoo.tools import OrderedSet, LastOrderedSet
from .exception import NoComponentError, SeveralComponentError


# this is duplicated from odoo.models.MetaModel._get_addon_name() which we
# unfortunately can't use because it's an instance method and should have been
# a @staticmethod
def _get_addon_name(full_name):
    # The (OpenERP) module name can be in the ``odoo.addons`` namespace
    # or not. For instance, module ``sale`` can be imported as
    # ``odoo.addons.sale`` (the right way) or ``sale`` (for backward
    # compatibility).
    module_parts = full_name.split('.')
    if len(module_parts) > 2 and module_parts[:2] == ['odoo', 'addons']:
        addon_name = full_name.split('.')[2]
    else:
        addon_name = full_name.split('.')[0]
    return addon_name


class ComponentGlobalRegistry(OrderedDict):
    """ Store all the components by name

    Allow to _inherit components.

    Another registry allow to register components on a
    particular collection and to find them back.

    This is an OrderedDict, because we want to keep the
    registration order of the components, addons loaded first
    have their components found first (when we look for a list
    components using `multi`).

    """


all_components = ComponentGlobalRegistry()


class WorkContext(object):

    def __init__(self, collection, model_name, **kwargs):
        self.collection = collection
        self.model_name = model_name
        self.model = self.env[model_name]
        self._propagate_kwargs = []
        for attr_name, value in kwargs.iteritems():
            setattr(self, attr_name, value)
            self._propagate_kwargs.append(attr_name)

    @property
    def env(self):
        return self.collection.env

    def work_on(self, model_name):
        kwargs = {attr_name: getattr(self, attr_name)
                  for attr_name in self._propagate_kwargs}
        return self.__class__(self.collection, model_name, **kwargs)

    def component_by_name(self, name):
        return all_components['base'](self).component_by_name(name)

    def components(self, usage=None, model_name=None, multi=False):
        return all_components['base'](self).components(
            usage=usage,
            model_name=model_name,
            multi=multi,
        )

    def __str__(self):
        return "WorkContext(%s,%s)" % (repr(self.collection), self.model_name)

    def __unicode__(self):
        return unicode(str(self))

    __repr__ = __str__


class MetaComponent(type):

    _modules_components = defaultdict(list)

    def __init__(self, name, bases, attrs):
        if not self._register:
            self._register = True
            super(MetaComponent, self).__init__(name, bases, attrs)
            return

        if not hasattr(self, '_module'):
            self._module = _get_addon_name(self.__module__)

        self._modules_components[self._module].append(self)

    @property
    def apply_on_models(self):
        # None means all models
        if self._apply_on is None:
            return None
        # always return a list, used for the lookup
        elif isinstance(self._apply_on, basestring):
            return [self._apply_on]
        return self._apply_on


class AbstractComponent(object):
    __metaclass__ = MetaComponent

    _register = False
    _abstract = True

    _name = None
    _inherit = None

    # name of the collection to subscribe in
    _collection = None

    _apply_on = None  # None means any Model, can be a list ['res.users', ...]
    _usage = None  # component purpose ('import.mapper', ...)

    def __init__(self, work_context):
        super(AbstractComponent, self).__init__()
        self.work = work_context

    @property
    def collection(self):
        return self.work.collection

    @property
    def env(self):
        return self.collection.env

    @property
    def model(self):
        return self.work.model

    # TODO use a LRU cache (repoze.lru, beware we must include the collection
    # name in the cache but not 'self')
    @staticmethod  # staticmethod in order to use a LRU cache on all args
    def lookup(collection_name, usage=None, model_name=None, multi=False):
        # TODO: verify that ordering is kept

        # keep the order so addons loaded first have components used first
        # in case of multi=True
        collection_components = [
            component for component in all_components.itervalues()
            if (component._collection == collection_name or
                component._collection is None) and
            not component._abstract
        ]
        candidates = []

        if usage is not None:
            components = [component for component in collection_components
                          if component._usage == usage]
            if components:
                candidates = components
        else:
            candidates = collection_components.values()

        # filter out by model name
        candidates = [c for c in candidates
                      if c.apply_on_models is None or
                      model_name in c.apply_on_models]

        if not candidates:
            raise NoComponentError(
                "No component found for collection '%s', "
                "usage '%s', model_name '%s'." %
                (collection_name, usage, model_name)
            )

        if not multi:
            if len(candidates) > 1:
                raise SeveralComponentError(
                    "Several components found for collection '%s', "
                    "usage '%s', model_name '%s'. Found: %r" %
                    (collection_name, usage, model_name, candidates)
                )
            # TODO: always return a list here, use a 2 methods for multi/normal
            return candidates.pop()

        return candidates

    def _component_class_by_name(self, name):
        component_class = all_components.get(name)
        if not component_class:
            # TODO: which error type?
            raise ValueError("No component with name '%s' found." % name)
        return component_class

    def component_by_name(self, name, model_name=None):
        if model_name is None or model_name == self.work.model_name:
            work_context = self.work
        else:
            work_context = self.work.work_on(model_name)

        component_class = self._component_class_by_name(name)
        return component_class(work_context)

    def components(self, usage=None, model_name=None, multi=False):
        if isinstance(model_name, models.BaseModel):
            model_name = model_name._name
        component_class = self.lookup(
            self.collection._name,
            usage=usage,
            model_name=model_name or self.work.model_name,
            multi=multi,
        )
        if model_name is None or model_name == self.work.model_name:
            work_context = self.work
        else:
            work_context = self.work.work_on(model_name)
        return component_class(work_context)

    def __str__(self):
        return "Component(%s)" % self._name

    def __unicode__(self):
        return unicode(str(self))

    __repr__ = __str__

    #
    # Goal: try to apply inheritance at the instantiation level and
    #       put objects in the registry var
    #
    @classmethod
    def _build_component(cls, registry):
        """ Instantiate a given Component in the registry.

        This method creates or extends a "registry" class for the given
        component.
        This "registry" class carries inferred component metadata, and inherits
        (in the Python sense) from all classes that define the component, and
        possibly other registry classes.

        """

        # In the simplest case, the component's registry class inherits from
        # cls and the other classes that define the component in a flat
        # hierarchy.  The registry contains the instance ``component`` (on the
        # left). Its class, ``ComponentClass``, carries inferred metadata that
        # is shared between all the component's instances for this registry
        # only.
        #
        #   class A1(Component):                    Component
        #       _name = 'a'                           / | \
        #                                            A3 A2 A1
        #   class A2(Component):                      \ | /
        #       _inherit = 'a'                    ComponentClass
        #
        #   class A3(Component):
        #       _inherit = 'a'
        #
        # When a component is extended by '_inherit', its base classes are
        # modified to include the current class and the other inherited
        # component classes.
        # Note that we actually inherit from other ``ComponentClass``, so that
        # extensions to an inherited component are immediately visible in the
        # current component class, like in the following example:
        #
        #   class A1(Component):
        #       _name = 'a'                          Component
        #                                            /  / \  \
        #   class B1(Component):                    /  A2 A1  \
        #       _name = 'b'                        /   \  /    \
        #                                         B2 ComponentA B1
        #   class B2(Component):                   \     |     /
        #       _name = 'b'                         \    |    /
        #       _inherit = ['a', 'b']                \   |   /
        #                                            ComponentB
        #   class A2(Component):
        #       _inherit = 'a'

        # determine inherited components
        parents = cls._inherit
        if isinstance(parents, basestring):
            parents = [parents]
        elif parents is None:
            parents = []

        if cls._name in registry:
            raise TypeError('Component %r (in class %r) already exists. '
                            'Consider using _inherit instead of _name '
                            'or using a different _name.' % (cls._name, cls))

        # determine the component's name
        name = cls._name or (len(parents) == 1 and parents[0])

        # all components except 'base' implicitly inherit from 'base'
        if name != 'base':
            parents = list(parents) + ['base']

        # create or retrieve the component's class
        if name in parents:
            if name not in registry:
                raise TypeError("Component %r does not exist in registry." %
                                name)
            ComponentClass = registry[name]
        else:
            ComponentClass = type(
                name, (AbstractComponent,),
                {'_name': name,
                 '_register': False,
                 # names of children component
                 '_inherit_children': OrderedSet()},
            )

        # determine all the classes the component should inherit from
        bases = LastOrderedSet([cls])
        for parent in parents:
            if parent not in registry:
                raise TypeError(
                    "Component %r inherits from non-existing component %r." %
                    (name, parent)
                )
            parent_class = registry[parent]
            if parent == name:
                for base in parent_class.__bases__:
                    bases.add(base)
            else:
                bases.add(parent_class)
                parent_class._inherit_children.add(name)
        ComponentClass.__bases__ = tuple(bases)

        ComponentClass._complete_component_build()

        registry[name] = ComponentClass

        return ComponentClass

    @classmethod
    def _complete_component_build(cls):
        """ Complete build of the new component class

        After the component has been built from its bases, this method is
        called, and can be used to customize the class before it can be used.

        Nothing is done in the base Component, but a Component can inherit
        the method to add its own behavior.
        """


class Component(AbstractComponent):
    _register = False
    _abstract = False