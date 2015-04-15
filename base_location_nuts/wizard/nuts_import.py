# -*- coding: utf-8 -*-
# Python source code encoding : https://www.python.org/dev/peps/pep-0263/
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    This module copyright :
#        (c) 2015 Antiun Ingenieria, SL (Madrid, Spain, http://www.antiun.com)
#                 Antonio Espinosa <antonioea@antiun.com>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from openerp import models, api, _
from openerp.exceptions import Warning
import requests
import re
import logging
from lxml import etree
from collections import OrderedDict

from pprint import pformat

logger = logging.getLogger(__name__)


class NutsImport(models.TransientModel):
    _name = 'nuts.import'
    _description = 'Import NUTS items from European RAMON service'
    _parents = [False, False, False, False]
    _countries = {
        "BE": False,
        "BG": False,
        "CZ": False,
        "DK": False,
        "DE": False,
        "EE": False,
        "IE": False,
        "GR": False,  # EL
        "ES": False,
        "FR": False,
        "HR": False,
        "IT": False,
        "CY": False,
        "LV": False,
        "LT": False,
        "LU": False,
        "HU": False,
        "MT": False,
        "NL": False,
        "AT": False,
        "PL": False,
        "PT": False,
        "RO": False,
        "SI": False,
        "SK": False,
        "FI": False,
        "SE": False,
        "GB": False,  # UK
    }
    _current_country = False
    _map = OrderedDict([
        ('level', {
            'xpath': '', 'attrib': 'idLevel',
            'type': 'integer', 'required': True}),
        ('code', {
            'xpath': './Label/LabelText[@language="ALL"]',
            'type': 'string', 'required': True}),
        ('name', {
            'xpath': './Label/LabelText[@language="EN"]',
            'type': 'string', 'required': True}),
    ])

    def _check_node(self, node):
        if node.get('id') and node.get('idLevel'):
            return True
        return False

    def _mapping(self, node):
        item = {}
        for k, v in self._map.iteritems():
            field_xpath = v.get('xpath', '')
            field_attrib = v.get('attrib', False)
            field_type = v.get('type', 'string')
            field_required = v.get('required', False)
            value = ''
            if field_xpath:
                n = node.find(field_xpath)
            else:
                n = node
            if n is not None:
                if field_attrib:
                    value = n.get(field_attrib, '')
                else:
                    value = n.text
                if field_type == 'integer':
                    try:
                        value = int(value)
                    except:
                        value = 0
            else:
                logger.debug("xpath = '%s', not found" % field_xpath)
            if field_required and not value:
                raise Warning(
                    _('Value not found for mandatory field %s' % k))
            item[k] = value
        return item

    def _download_nuts(self):
        url_base = 'http://ec.europa.eu'
        url_path = '/eurostat/ramon/nomenclatures/index.cfm'
        url_params = {
            'TargetUrl': 'ACT_OTH_CLS_DLD',
            'StrNom': 'NUTS_2013',
            'StrFormat': 'XML',
            'StrLanguageCode': 'EN',
            'StrLayoutCode': 'HIERARCHIC'
        }
        url = url_base + url_path + '?'
        url += '&'.join([k + '=' + v for k, v in url_params.iteritems()])
        logger.info('Starting to download %s' % url)
        try:
            res_request = requests.get(url)
        except Exception, e:
            raise Warning(
                _('Got an error when trying to download the file: %s.') %
                str(e))
        if res_request.status_code != requests.codes.ok:
            raise Warning(
                _('Got an error %d when trying to download the file %s.')
                % (res_request.status_code, url))
        logger.info('Download successfully %d bytes' %
                    len(res_request.content))
        # Workaround XML: Remove all characters before <?xml
        pattern = re.compile(r'^.*<\?xml', re.DOTALL)
        content_fixed = re.sub(pattern, '<?xml', res_request.content)
        if not re.match(r'<\?xml', content_fixed):
            raise Warning(_('Downloaded file is not a valid XML file'))
        return content_fixed

    @api.model
    def _load_countries(self):
        for k in self._countries.keys():
            self._countries[k] = self.env['res.country'].search(
                [('code', '=', k)])
        # Workaround to translate some country codes:
        #   EL => GR (Greece)
        #   UK => GB (United Kingdom)
        self._countries['EL'] = self._countries['GR']
        self._countries['UK'] = self._countries['GB']
        logger.info('_load_countries = %s' % pformat(self._countries))

    @api.model
    def state_mapping(self, data, node):
        # Method to inherit and add state_id relation depending on country
        level = data.get('level', 0)
        code = data.get('code', '')
        if level == 1:
            self._current_country = self._countries[code]
        return {
            'country_id': self._current_country.id,
        }

    @api.model
    def create_or_update_nuts(self, node):
        if not self._check_node(node):
            return False

        nuts_model = self.env['res.partner.nuts']
        data = self._mapping(node)
        data.update(self.state_mapping(data, node))
        level = data.get('level', 0)
        if level >= 2 and level <= 5:
            data['parent_id'] = self._parents[level - 2]
        nuts = nuts_model.search([('level', '=', data['level']),
                                  ('code', '=', data['code'])])
        if nuts:
            nuts.write(data)
        else:
            nuts = nuts_model.create(data)
        if level >= 1 and level <= 4:
            self._parents[level - 1] = nuts.id
        return nuts

    @api.one
    def run_import(self):
        nuts_model = self.env['res.partner.nuts'].\
            with_context(defer_parent_store_computation=True)
        self._load_countries()
        # All current NUTS (for available countries),
        #   delete if not found above
        nuts_to_delete = nuts_model.search(
            [('country_id', 'in', [x.id for x in self._countries.values()])])
        # Download NUTS in english, create or update
        logger.info('Import NUTS 2013 English')
        xmlcontent = self._download_nuts()
        dom = etree.fromstring(xmlcontent)
        for node in dom.iter('Item'):
                logger.info('Reading level=%s, id=%s' %
                            (node.get('idLevel', 'N/A'),
                             node.get('id', 'N/A')))
                nuts = self.create_or_update_nuts(node)
                if nuts and nuts in nuts_to_delete:
                    nuts_to_delete -= nuts
        # Delete obsolete NUTS
        if nuts_to_delete:
            logger.info('%d NUTS entries deleted' % len(nuts_to_delete))
            nuts_to_delete.unlink()
        logger.info(
            'The wizard to create NUTS entries from RAMON '
            'has been successfully completed.')

        return True