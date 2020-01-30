# -*- coding: utf-8 -*-

import requests
import traceback

from ckan.lib.helpers import json
from ckan.lib.munge import munge_tag
from ckanext.harvest.model import HarvestObject
from ckanext.harvest.harvesters import HarvesterBase
from ckanext.ddi.importer import DdiCkanMetadata

from ckantoolkit import config

import logging
log = logging.getLogger(__name__)


class NadaHarvester(HarvesterBase):
    '''
    The harvester for DDI data
    '''

    HARVEST_USER = 'harvest'

    ACCESS_TYPES = {
        '': '',
        'direct_access': 1,
        'public_use': 2,
        'licensed': 3,
        'data_enclave': 4,
        'data_external': 5,
        'no_data_available': 6,
        'open_data': 7,
    }
    
    # CKAN attributes, but we're missing heaps (they'd all just be stored in 'extras')
    #   could be a big problem point
    DEFAULT_ATTRIBUTES = [
        'id',
        'name',
        'title',
        'url',
        'author',
        'author_email',
        'maintainer',
        'maintainer_email',
        'license_id',
        'version',
        'notes',
        'tags',
        'extras',
    ]

    def info(self):
        return {
            'name': 'nada',
            'title': 'NADA harvester for DDI',
            'description': (
                'Harvests DDI data from a NADA instance '
                '(survey cataloguing software).'
            ),
            'form_config_interface': 'Text'
        }

    def _set_config(self, config_str):
        if config_str:
            self.config = json.loads(config_str)
        else:
            self.config = {}

        if 'user' not in self.config:
            self.config['user'] = self.HARVEST_USER

        log.debug('Using config: %r' % self.config)

    def _get_search_api(self, access_type=None, page=1):
        if access_type is not None:
            try:
                return (
                    '/index.php/api/v2/catalog/search/format/json/'
                    '?dtype[]=%s&page=%s'
                ) % (self.ACCESS_TYPES[access_type], page)
            except KeyError:
                raise AccessTypeNotAvailableError(
                    'Access type %s not available. Available types: %s'
                    % (access_type, self.ACCESS_TYPES)
                )
        else:
            return (
                '/index.php/api/v2/catalog/search/format/json?page=%s'
                % page
            )

    def _get_ddi_api(self, ddi_id):
        return '/index.php/catalog/ddi/%s' % ddi_id

    def _get_catalog_path(self, ddi_id):
        return '/index.php/catalog/%s' % ddi_id

    def gather_stage(self, harvest_job):
        log.debug('In NadaHarvester gather_stage')
        api_url = None
        
        # Go to i.e. https://microdata.pacificdata.org/index.php/api/v2/catalog/search/format/json?page=1
        #   and get the json
        '''
        {"rows":[{"id":"639","idno":"SPC_ASM_2018_BAS_v01_M_DEVELOPMENT","title":"Business Survey 2018",
                  "nation":"American Samoa","authoring_entity":"American Samoa Department of Commerce",
                  "form_model":"data_na","year_start":"2018","year_end":"2018","repositoryid":"ASM",
                  "link_da":null,"repo_title":"American Samoa","created":"1566780425","changed":"1566780813",
                  "total_views":"23","total_downloads":"0"},
                 {"id":"703","idno":"SPC_ASM_2017_ECS_v01_M_DEVELOPMENT",
                  "title":"Economic census 2017","nation":"American Samoa","authoring_entity":"US Census ....}]}
        '''
        # For each row of data, use its ID as the GUID and save a harvest job
        # Return a list of all these new harvest jobs
        # 
        try:
            continue_gather = True
            page = 1
            harvest_obj_ids = []
            while continue_gather:
                self._set_config(harvest_job.source.config)
                base_url = harvest_job.source.url.rstrip('/')

                try:
                    api_url = base_url + self._get_search_api(
                        self.config['access_type'],
                        page
                    )
                except (AccessTypeNotAvailableError, KeyError):
                    api_url = base_url + self._get_search_api(
                        'public_use',
                        page
                    )

                log.debug('Gather datasets from: %s' % api_url)

                headers = {
                    'User-agent': 'Mozilla/5.0'
                }
                r = requests.get(api_url, headers=headers)
                data = r.json()

                log.debug('JSON data from %s: %r' % (api_url, data))

                for row in data['rows']:
                    harvest_obj = HarvestObject(
                        guid=row['id'],
                        job=harvest_job
                    )
                    harvest_obj.save()
                    harvest_obj_ids.append(harvest_obj.id)
                page += 1
                row_count = int(data['offset']) + int(data['limit'])
                continue_gather = row_count < int(data['found'])

            log.debug('IDs: %r' % harvest_obj_ids)

            return harvest_obj_ids
        except Exception as e:
            self._save_gather_error(
                'Unable to get content for URL: %s: %s / %s'
                % (api_url, str(e), traceback.format_exc()),
                harvest_job
            )
    
    # Get the DDI formatted resource for the GUID
    #   i.e. https://microdata.pacificdata.org/index.php/catalog/ddi/639
    #   returns a download containing DDI resource
    # Put this in harvest_object's 'content' as text
    def fetch_stage(self, harvest_object):
        log.debug('In NadaHarvester fetch_stage')
        self._set_config(harvest_object.job.source.config)

        if not harvest_object:
            log.error('No harvest object received')
            self._save_object_error(
                'No harvest object received',
                harvest_object
            )
            return False

        base_url = harvest_object.source.url.rstrip('/')
        ddi_api_url = None
        try:
            ddi_api_url = base_url + self._get_ddi_api(harvest_object.guid)
            log.debug('Fetching content from %s' % ddi_api_url)
            headers = {
                'User-agent': 'Mozilla/5.0'
            }
            r = requests.get(ddi_api_url, headers=headers)
            r.encoding = 'utf-8'
            harvest_object.content = r.text
            harvest_object.save()
            log.debug('successfully processed ' + harvest_object.guid)
            return True
        except Exception as e:
            self._save_object_error(
                (
                    'Unable to get content for package: %s: %r / %s'
                    % (ddi_api_url, e, traceback.format_exc())
                ),
                harvest_object
            )
            return False

    def import_stage(self, harvest_object):
        log.debug('In NadaHarvester import_stage')
        self._set_config(harvest_object.job.source.config)

        if not harvest_object:
            log.error('No harvest object received')
            self._save_object_error(
                'No harvest object received',
                harvest_object
            )
            return False

        try:
            base_url = harvest_object.source.url.rstrip('/')
            
            # Get a class which maps ckan metadata to the DDI equivalent
            ckan_metadata = DdiCkanMetadata()
            # Extract metadata content from XML DDI
            #   put it in a dictionary
            pkg_dict = ckan_metadata.load(harvest_object.content)
            
            # Go through the dictionary and put 'uncrecognised' attributes
            #   into a field called 'extras' (any field which isn't in DEFAULT ATTRIBUTES)
            pkg_dict = self._convert_to_extras(pkg_dict)

            # update URL with NADA catalog link
            catalog_path = self._get_catalog_path(harvest_object.guid)
            pkg_dict['url'] = base_url + catalog_path

            # set license from harvester config or use CKAN instance default
            if 'license' in self.config:
                pkg_dict['license_id'] = self.config['license']
            else:
                pkg_dict['license_id'] = config.get(
                    'ckanext.ddi.default_license',
                    ''
                )
            # Add tags if necessary   
            tags = []
            for tag in pkg_dict['tags']:
                if isinstance(tag, basestring):
                    tags.append(munge_tag(tag[:100]))
            pkg_dict['tags'] = tags
            pkg_dict['version'] = pkg_dict['version'][:100]

            # add resources
            # basically sources
            resources = [
                {
                    'url': base_url + self._get_ddi_api(harvest_object.guid),
                    'name': 'DDI XML of %s' % pkg_dict['title'],
                    'format': 'xml'
                },
                {
                    'url': pkg_dict['url'],
                    'name': 'NADA catalog entry',
                    'format': 'html'
                },
            ]
            pkg_dict['resources'] = resources

            log.debug('package dict: %s' % pkg_dict)
            # Now create the package
            return self._create_or_update_package(pkg_dict, harvest_object)
        except Exception as e:
            self._save_object_error(
                (
                    'Exception in import stage: %r / %s'
                    % (e, traceback.format_exc())
                ),
                harvest_object
            )
            return False

    def _convert_to_extras(self, pkg_dict):
        if 'extras' not in pkg_dict:
            pkg_dict['extras'] = []
        keys_to_delete = []
        for key in pkg_dict:
            if key not in self.DEFAULT_ATTRIBUTES:
                log.debug('Converting %s to extra' % key)
                pkg_dict['extras'].append((key, pkg_dict[key]))
                keys_to_delete.append(key)

        for key in keys_to_delete:
            if key in pkg_dict:
                log.debug('Delete key %s from pkg_dict' % key)
                del pkg_dict[key]
        return pkg_dict


class AccessTypeNotAvailableError(Exception):
    pass
