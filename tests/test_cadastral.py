import unittest

from core.cadastral import cadastral_feature_info_params, parse_cadastral_feature_info


class CadastralFeatureInfoTests(unittest.TestCase):
    def test_parse_kieg_feature_info_table(self):
        html = """
        <table class="getfeatureinfo-egib">
            <tr><td>Identyfikator działki</td><td>026401_1.0022.AR_27.87</td></tr>
            <tr><td>Nazwa gminy</td><td>Wrocław</td></tr>
            <tr><td>Nazwa obrębu</td><td>Południe</td></tr>
            <tr><td>Numer działki</td><td>87</td></tr>
            <tr><td>Pole pow. w ewidencji gruntów (ha)</td><td>0.5964</td></tr>
            <tr><td>Grupa rejestrowa</td><td>7.1</td></tr>
            <tr><td>Data publikacji danych</td><td>2026-06-05</td></tr>
        </table>
        """

        parcel = parse_cadastral_feature_info(html)

        self.assertEqual(parcel["parcel_id"], "026401_1.0022.AR_27.87")
        self.assertEqual(parcel["municipality"], "Wrocław")
        self.assertEqual(parcel["district"], "Południe")
        self.assertEqual(parcel["parcel_number"], "87")
        self.assertEqual(parcel["area_ha"], "0.5964")
        self.assertEqual(parcel["registry_group"], "7.1")
        self.assertEqual(parcel["published_at"], "2026-06-05")
        self.assertEqual(parcel["raw_fields"]["Numer działki"], "87")

    def test_feature_info_params_use_fixed_kieg_query_shape(self):
        params = cadastral_feature_info_params(51.089742, 17.03894)

        self.assertEqual(params["SERVICE"], "WMS")
        self.assertEqual(params["REQUEST"], "GetFeatureInfo")
        self.assertEqual(params["VERSION"], "1.3.0")
        self.assertEqual(params["LAYERS"], "dzialki")
        self.assertEqual(params["QUERY_LAYERS"], "dzialki")
        self.assertEqual(params["CRS"], "EPSG:3857")
        self.assertEqual(params["I"], "256")
        self.assertEqual(params["J"], "256")
        self.assertIn("1896", params["BBOX"])


if __name__ == "__main__":
    unittest.main()

